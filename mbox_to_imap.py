#!/usr/bin/env python3
"""
mbox_to_imap.py

Importiert die von "readpst -r" erzeugten mbox-Dateien (eine "mbox"-Datei
pro Ordner, Ordnerstruktur = Ordnerbaum der ursprünglichen PST) per IMAP
APPEND auf einen Ziel-Mailserver, z.B. Synology MailPlus Server.

Vorgehen:
  1. Verbindet sich per IMAP (SSL) mit dem Zielserver.
  2. Ermittelt automatisch das Hierarchie-Trennzeichen des Servers
     (bei MailPlus i.d.R. ".").
  3. Durchsucht das Quellverzeichnis rekursiv nach "mbox"-Dateien.
  4. Legt für jeden gefundenen Ordner (falls nötig) einen IMAP-Ordner an
     und importiert alle Nachrichten per APPEND, inkl. Original-Datum.
  5. Merkt sich abgeschlossene Ordner in einer Fortschrittsdatei, damit
     das Skript bei einem Abbruch einfach erneut gestartet werden kann
     (bereits fertige Ordner werden dann übersprungen).

Aufruf-Beispiel:
    python3 mbox_to_imap.py \
        --source ~/pst-export/Archiv \
        --host nas-hostname.synology.me \
        --port 993 \
        --user benutzername \
        --dry-run

Erst mit --dry-run testen (zählt nur, importiert nichts), dann ohne
--dry-run für den echten Import. Das Passwort wird interaktiv abgefragt
und nirgends gespeichert.

Weitere Optionen:
  --prefix <name>   Legt alle Ordner gebuendelt unter einem gemeinsamen
                     Ueberordner an, statt als eigenstaendige Ordner auf
                     oberster Ebene. Beispiel: --prefix "Archiv" importiert
                     einen Quellordner "OSILAB/SWISSCOM/ComTec" als
                     IMAP-Ordner "Archiv.OSILAB.SWISSCOM.ComTec" statt als
                     "OSILAB.SWISSCOM.ComTec". Nuetzlich, um mehrere
                     Importlaeufe (z.B. verschiedene PST-Dateien) sauber
                     voneinander bzw. von echten, aktuellen Mails zu trennen.

  --list-targets    Verbindet sich nur lesend (nur fuer das Trennzeichen),
                     erstellt/aendert nichts, sondern gibt lediglich die
                     Ziel-IMAP-Ordnernamen aus, die dieser Lauf anlegen/
                     verwenden wuerde -- inkl. eines evtl. gesetzten
                     --prefix. Praktisch zum Kontrollieren vor dem
                     eigentlichen Import, oder als Eingabe fuer
                     cleanup_imap_folders.py --only-from-file.

Beispiel mit Prefix:
    python3 mbox_to_imap.py \
        --source ~/pst-export/osilab_export \
        --host nas-hostname.synology.me \
        --user benutzername \
        --prefix "Archiv" \
        --list-targets
"""

import argparse
import base64
import getpass
import imaplib
import os
import re
import sys
import time
from email import message_from_bytes
from email.utils import parsedate_tz, mktime_tz

PROGRESS_FILE = ".mbox_to_imap_progress.txt"

# Erkennt die mbox-Trennzeile "From <absender> <datum>" am Zeilenanfang.
# mbox-Dateien escapen interne "From "-Zeilen im Nachrichtentext immer mit
# einem ">" davor, daher ist dieses Muster eindeutig fuer echte Trennzeilen.
_MBOX_FROM_LINE = re.compile(rb"(?m)^From [^\r\n]*\r?\n")


def iterate_mbox_messages(mbox_path):
    """Liest eine mbox-Datei roh als Bytes und liefert (index, email.Message)
    Paare. Vermeidet bewusst Pythons mailbox.mbox-Klasse, die bei
    Nicht-ASCII-Zeichen (z.B. Umlauten) in der internen From-Zeile mit
    UnicodeDecodeError abstuerzt."""
    with open(mbox_path, "rb") as f:
        data = f.read()

    matches = list(_MBOX_FROM_LINE.finditer(data))
    if not matches:
        return

    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(data)
        raw = data[start:end]
        if not raw.strip():
            continue
        try:
            msg = message_from_bytes(raw)
        except Exception:
            continue
        yield i, msg


def imap_utf7_encode(name):
    """Kodiert einen String nach dem 'Modified UTF-7'-Verfahren, das IMAP
    fuer Mailbox-Namen mit Nicht-ASCII-Zeichen vorschreibt (RFC 3501)."""
    result = []
    buf = []

    def flush():
        if buf:
            raw = "".join(buf).encode("utf-16-be")
            b64 = base64.b64encode(raw).decode("ascii").rstrip("=")
            b64 = b64.replace("/", ",")
            result.append("&" + b64 + "-")
            buf.clear()

    for ch in name:
        code = ord(ch)
        if ch == "&":
            flush()
            result.append("&-")
        elif 0x20 <= code <= 0x7E:
            flush()
            result.append(ch)
        else:
            buf.append(ch)
    flush()
    return "".join(result)


def to_imap_mailbox_arg(name):
    """Kodiert (Modified UTF-7) und quotet einen Ordnernamen, damit er
    als IMAP-Kommando-Argument sicher verwendet werden kann (auch bei
    Leerzeichen, Umlauten, Sonderzeichen). WICHTIG: MailPlus/Dovecot lehnt
    rohes UTF-8 in Mailbox-Namen mit 'not valid mUTF-7' ab -- die
    Modified-UTF-7-Kodierung ist hier zwingend erforderlich, nicht optional."""
    encoded = imap_utf7_encode(name)
    escaped = encoded.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped + '"'


def load_progress(progress_path):
    if not os.path.exists(progress_path):
        return set()
    with open(progress_path, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def mark_done(progress_path, folder_key):
    with open(progress_path, "a", encoding="utf-8") as f:
        f.write(folder_key + "\n")


def imap_safe_name(name, delimiter="."):
    """Ersetzt Zeichen, die in IMAP-Ordnernamen Probleme machen: '/' (falls
    im Namen vorhanden) und das jeweilige Server-Trennzeichen selbst (z.B.
    '.'), da sonst z.B. ein Domainname wie 'onlime.ch' vom Server faelschlich
    als zwei verschachtelte Ordner 'onlime' -> 'ch' interpretiert wird."""
    result = name.replace("/", "-")
    if delimiter:
        result = result.replace(delimiter, "-")
    return result.strip()


def find_mbox_folders(source_root):
    """Liefert (relativer_ordnerpfad, absoluter_pfad_zur_mbox_datei) für
    jede gefundene 'mbox'-Datei unterhalb von source_root."""
    results = []
    for dirpath, _dirnames, filenames in os.walk(source_root):
        if "mbox" in filenames:
            mbox_path = os.path.join(dirpath, "mbox")
            rel_dir = os.path.relpath(dirpath, source_root)
            results.append((rel_dir, mbox_path))
    return results


def build_imap_folder_name(rel_dir, delimiter, prefix):
    if rel_dir in (".", ""):
        parts = ["Archiv"]
    else:
        parts = [imap_safe_name(p, delimiter) for p in rel_dir.split(os.sep) if p]
    name = delimiter.join(parts)
    if prefix:
        name = prefix + delimiter + name
    return name


def get_delimiter(imap_conn):
    typ, data = imap_conn.list()
    if typ == "OK" and data and data[0]:
        # Format je Zeile: (\Flags) "DELIM" "Name"
        entry = data[0].decode(errors="replace")
        try:
            delim = entry.split('"')[1]
            if delim:
                return delim
        except IndexError:
            pass
    return "."  # Fallback, passt zu MailPlus-Standardverhalten


def ensure_folder(imap_conn, folder_name):
    mailbox_arg = to_imap_mailbox_arg(folder_name)
    typ, _ = imap_conn.select(mailbox_arg, readonly=True)
    if typ == "OK":
        imap_conn.close()
        return True
    typ, resp = imap_conn.create(mailbox_arg)
    if typ != "OK":
        print(f"  WARNUNG: Ordner konnte nicht angelegt werden: {folder_name} -> {resp}")
        return False
    return True


def append_message(imap_conn, folder_name, raw_bytes, date_header):
    internaldate = None
    if date_header:
        try:
            parsed = parsedate_tz(date_header)
            if parsed:
                ts = mktime_tz(parsed)
                internaldate = imaplib.Time2Internaldate(ts)
        except Exception:
            internaldate = None
    mailbox_arg = to_imap_mailbox_arg(folder_name)
    typ, resp = imap_conn.append(mailbox_arg, "", internaldate, raw_bytes)
    return typ == "OK", resp


def main():
    parser = argparse.ArgumentParser(description="mbox (readpst -r) -> IMAP Import")
    parser.add_argument("--source", required=True, help="Wurzelverzeichnis mit den mbox-Ordnern (z.B. ~/pst-export/Archiv)")
    parser.add_argument("--host", required=True, help="IMAP-Server, z.B. nas-hostname.synology.me")
    parser.add_argument("--port", type=int, default=993, help="IMAP-Port (Standard: 993)")
    parser.add_argument("--user", required=True, help="IMAP-Benutzername")
    parser.add_argument("--prefix", default="",
                         help="Optionaler gemeinsamer IMAP-Ueberordner, unter dem ALLE importierten "
                              "Ordner gebuendelt angelegt werden, z.B. --prefix \"Archiv\" macht aus "
                              "\"OSILAB.SWISSCOM\" den Ordner \"Archiv.OSILAB.SWISSCOM\". "
                              "Standard: kein Prefix, Ordner landen auf oberster Ebene.")
    parser.add_argument("--dry-run", action="store_true", help="Nur zählen, nichts importieren")
    parser.add_argument("--list-targets", action="store_true",
                         help="Nur die Ziel-IMAP-Ordnernamen ausgeben (verbindet sich fuer das "
                              "Trennzeichen, erstellt/aendert aber nichts). Zum Umleiten in eine "
                              "Datei fuer ein gezieltes Aufraeumen, z.B. mit cleanup_imap_folders.py "
                              "--only-from-file.")
    args = parser.parse_args()

    source_root = os.path.expanduser(args.source)
    if not os.path.isdir(source_root):
        print(f"Quellverzeichnis nicht gefunden: {source_root}")
        sys.exit(1)

    folders = find_mbox_folders(source_root)
    if not folders:
        print("Keine mbox-Dateien gefunden. Stimmt der --source Pfad?")
        sys.exit(1)

    print(f"{len(folders)} Ordner mit mbox-Dateien gefunden.")

    if args.dry_run:
        total = 0
        for rel_dir, mbox_path in folders:
            count = sum(1 for _ in iterate_mbox_messages(mbox_path))
            total += count
            print(f"  {rel_dir}: {count} Nachrichten")
        print(f"Gesamt: {total} Nachrichten (Dry-Run, nichts wurde importiert)")
        return

    password = getpass.getpass(f"Passwort für {args.user}@{args.host}: ")

    if args.list_targets:
        print(f"Verbinde mit {args.host}:{args.port} (nur lesend, für Trennzeichen) ...")
        imap_conn = imaplib.IMAP4_SSL(args.host, args.port)
        imap_conn._encoding = "utf-8"
        imap_conn.login(args.user, password)
        delimiter = get_delimiter(imap_conn)
        imap_conn.logout()
        print(f"# Server-Trennzeichen: '{delimiter}' -- unten: Ziel-IMAP-Ordnernamen, die dieser", file=sys.stderr)
        print(f"# Importlauf anlegen/verwenden wuerde (nichts wurde veraendert).", file=sys.stderr)
        for rel_dir, _mbox_path in folders:
            print(build_imap_folder_name(rel_dir, delimiter, args.prefix))
        return

    print(f"Verbinde mit {args.host}:{args.port} ...")
    imap_conn = imaplib.IMAP4_SSL(args.host, args.port)
    # Workaround: imaplib nutzt standardmaessig ASCII-Encoding, was bei
    # Passwoertern mit Sonderzeichen (ö, ä, ü, etc.) zu einem
    # UnicodeEncodeError fuehrt. UTF-8 erzwingen, um das zu vermeiden.
    imap_conn._encoding = "utf-8"
    imap_conn.login(args.user, password)
    delimiter = get_delimiter(imap_conn)
    print(f"Server-Trennzeichen für Ordner: '{delimiter}'")

    progress_path = os.path.join(source_root, PROGRESS_FILE)
    done = load_progress(progress_path)

    grand_total_ok = 0
    grand_total_fail = 0

    for rel_dir, mbox_path in folders:
        if rel_dir in done:
            print(f"[uebersprungen] {rel_dir} (bereits erledigt laut Fortschrittsdatei)")
            continue

        imap_folder = build_imap_folder_name(rel_dir, delimiter, args.prefix)
        print(f"[Ordner] {rel_dir}  ->  IMAP: {imap_folder}")

        if not ensure_folder(imap_conn, imap_folder):
            grand_total_fail += 1
            continue

        ok_count = 0
        fail_count = 0

        for key, msg in iterate_mbox_messages(mbox_path):
            try:
                raw_bytes = msg.as_bytes()
            except Exception as e:
                print(f"    Nachricht {key} konnte nicht gelesen werden: {e}")
                fail_count += 1
                continue

            date_header = msg.get("Date")
            success, resp = append_message(imap_conn, imap_folder, raw_bytes, date_header)
            if success:
                ok_count += 1
            else:
                fail_count += 1
                print(f"    APPEND fehlgeschlagen fuer Nachricht {key}: {resp}")

        print(f"    -> {ok_count} importiert, {fail_count} fehlgeschlagen")
        grand_total_ok += ok_count
        grand_total_fail += fail_count

        if fail_count == 0:
            mark_done(progress_path, rel_dir)
        else:
            print(f"    Ordner NICHT als erledigt markiert (es gab Fehler) -> beim naechsten Lauf wird er wiederholt")

        # Kleine Pause, um den Server nicht zu ueberlasten
        time.sleep(0.2)

    imap_conn.logout()
    print("")
    print(f"Fertig. Insgesamt importiert: {grand_total_ok}, fehlgeschlagen: {grand_total_fail}")
    if grand_total_fail > 0:
        print("Skript einfach erneut starten, um fehlgeschlagene Ordner zu wiederholen.")


if __name__ == "__main__":
    main()
