#!/usr/bin/env python3
"""
fix_imap_folder_names.py

Findet IMAP-Ordner, deren Namen noch in "Modified UTF-7" kodiert sind
(z.B. "Auftr&AOQ-ge" statt "Aufträge" -- ein aelterer Zwischenstand dieser
Migration hat Ordner so benannt), und benennt sie per IMAP RENAME direkt
auf dem Server in Klartext (rohes UTF-8) um.

WICHTIG: Das aendert NUR den Ordnernamen. Der komplette Inhalt (bereits
importierte Archiv-Mails UND alle seither neu eingegangenen/einsortierten
Nachrichten in diesem Ordner) bleibt unangetastet -- es wird nichts
geloescht, nichts neu importiert, kein Datentransfer noetig.

Erkennt zusaetzlich das "Mojibake"-Muster (UTF-8-Bytes, die faelschlich
als Latin-1 interpretiert wurden) als Fallback, falls das ebenfalls
vorkommt.

Beispiel:
    # Erst nur anzeigen, was umbenannt wuerde:
    python3 fix_imap_folder_names.py --host nas-hostname.synology.me --user benutzername --dry-run

    # Dann wirklich umbenennen:
    python3 fix_imap_folder_names.py --host nas-hostname.synology.me --user benutzername
"""

import argparse
import base64
import getpass
import imaplib
import re
import sys

LIST_LINE_RE = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?P<delim>"[^"]*"|NIL)\s+(?P<name>.*)$')
_MOJIBAKE_HINT = re.compile(r"Ã.|Â.|â€")


def parse_list_line(line):
    m = LIST_LINE_RE.match(line)
    if not m:
        return None
    name = m.group("name").decode("utf-8", errors="replace").strip()
    return name


def unquote_mailbox(raw):
    """Entfernt IMAP-Quoting (Anfuehrungszeichen + Escaping), falls vorhanden."""
    s = raw
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        inner = s[1:-1]
        inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    return s


def quote_mailbox(name):
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped + '"'


def decode_imap_utf7(s):
    """Dekodiert 'Modified UTF-7' (RFC 3501) zurueck in normalen Unicode-Text.
    Gibt den unveraenderten String zurueck, wenn keine kodierten Abschnitte
    (kein '&...-') gefunden wurden."""
    if "&" not in s:
        return s
    result = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "&":
            j = s.find("-", i + 1)
            if j == -1:
                j = n
            seg = s[i + 1:j]
            if seg == "":
                result.append("&")
            else:
                b64 = seg.replace(",", "/")
                b64 += "=" * ((-len(b64)) % 4)
                try:
                    raw = base64.b64decode(b64)
                    result.append(raw.decode("utf-16-be"))
                except Exception:
                    # Kein gueltiges UTF-7-Segment -> unveraendert uebernehmen
                    result.append(s[i:j + 1])
            i = j + 1
        else:
            result.append(c)
            i += 1
    return "".join(result)


def repair_name(quoted_raw_name):
    """Nimmt den rohen Namen aus der LIST-Antwort (inkl. evtl. Anfuehrungs-
    zeichen) und gibt den korrigierten Klartextnamen zurueck, oder None,
    wenn keine Korrektur noetig ist."""
    unquoted = unquote_mailbox(quoted_raw_name)

    # 1. Versuch: echte Modified-UTF-7-Dekodierung
    decoded = decode_imap_utf7(unquoted)
    if decoded != unquoted:
        return decoded

    # 2. Fallback: Mojibake-Muster (Latin-1-Fehlinterpretation von UTF-8)
    if _MOJIBAKE_HINT.search(unquoted):
        try:
            repaired = unquoted.encode("latin-1").decode("utf-8")
            if repaired != unquoted:
                return repaired
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass

    return None


def main():
    parser = argparse.ArgumentParser(description="IMAP-Ordnernamen (UTF-7/Mojibake) reparieren, ohne Inhalte anzufassen")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=993)
    parser.add_argument("--user", required=True)
    parser.add_argument("--dry-run", action="store_true", help="Nur anzeigen, nichts umbenennen")
    args = parser.parse_args()

    password = getpass.getpass(f"Passwort für {args.user}@{args.host}: ")

    print(f"Verbinde mit {args.host}:{args.port} ...")
    imap_conn = imaplib.IMAP4_SSL(args.host, args.port)
    imap_conn._encoding = "utf-8"
    imap_conn.login(args.user, password)

    typ, data = imap_conn.list()
    if typ != "OK":
        print(f"Fehler beim Auflisten der Ordner: {data}")
        sys.exit(1)

    all_names = []
    for line in data:
        name = parse_list_line(line)
        if name:
            all_names.append(name)

    renames = []
    for raw_name in all_names:
        new_plain = repair_name(raw_name)
        if new_plain:
            renames.append((raw_name, new_plain))

    print(f"{len(all_names)} Ordner insgesamt gefunden, {len(renames)} muessen umbenannt werden.")

    if not renames:
        print("Nichts zu tun.")
        imap_conn.logout()
        return

    print("\nVorgeschlagene Umbenennungen (Inhalt bleibt unangetastet):")
    for old, new in renames:
        print(f"  {old}  ->  {new}")

    if args.dry_run:
        print("\n(Dry-Run: nichts wurde umbenannt)")
        imap_conn.logout()
        return

    # Tiefste Ordner zuerst umbenennen (mehr Trennzeichen = tiefer verschachtelt),
    # damit ein bereits umbenannter Elternordner nicht die Pfade der Kinder aendert.
    def depth(item):
        return unquote_mailbox(item[0]).count(".") + unquote_mailbox(item[0]).count("/")

    renames.sort(key=depth, reverse=True)

    ok_count = 0
    fail_count = 0
    for old_raw, new_plain in renames:
        old_arg = old_raw  # bereits korrekt gequotet, so wie vom Server geliefert
        new_arg = quote_mailbox(new_plain)
        typ, resp = imap_conn.rename(old_arg, new_arg)
        if typ == "OK":
            print(f"  umbenannt: {old_raw}  ->  {new_plain}")
            ok_count += 1
        else:
            print(f"  FEHLER bei {old_raw}: {resp}")
            fail_count += 1

    imap_conn.logout()
    print(f"\nFertig. {ok_count} umbenannt, {fail_count} fehlgeschlagen.")
    if fail_count:
        print("Bei Fehlern: Skript einfach erneut starten (z.B. wenn ein Elternordner "
              "erst nach seinen Kindern umbenannt werden konnte, oder der neue Name "
              "bereits existiert).")


if __name__ == "__main__":
    main()
