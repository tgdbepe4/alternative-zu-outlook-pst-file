#!/usr/bin/env python3
"""
rename_mojibake_folders.py

Findet IMAP-Ordner, deren Namen durch eine fehlerhafte Zeichenkodierung
verunstaltet wurden (z.B. "FlÃ¼ge" statt "Flüge" -- UTF-8-Bytes, die
irgendwo faelschlich als Latin-1/Windows-1252 interpretiert wurden), und
benennt sie per IMAP RENAME direkt auf dem Server um.

Wichtig: Das aendert NUR den Ordnernamen, nicht den Inhalt. Bereits
importierte Nachrichten bleiben unangetastet -- kein erneuter Datentransfer
noetig. Ordner, die kein Mojibake-Muster zeigen (z.B. INBOX oder neue,
echte Mail-Ordner), werden gar nicht erst angefasst.

Beispiel:
    # Erst nur anzeigen, was umbenannt wuerde:
    python3 rename_mojibake_folders.py --host nas-hostname.synology.me --user benutzername --dry-run

    # Dann wirklich umbenennen:
    python3 rename_mojibake_folders.py --host nas-hostname.synology.me --user benutzername
"""

import argparse
import getpass
import imaplib
import re
import sys

LIST_LINE_RE = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?P<delim>"[^"]*"|NIL)\s+(?P<name>.*)$')

# Typische Mojibake-Anzeichen: UTF-8-Mehrbyte-Sequenzen, die als
# Latin-1/Windows-1252-Einzelzeichen dargestellt wurden. Diese Zeichen
# tauchen in normalem deutschem/franzoesischem/spanischem Text so gut wie
# nie in dieser Kombination auf, daher ein zuverlaessiger Indikator.
_MOJIBAKE_HINT = re.compile(r"Ã.|Â.|â€")


def parse_list_line(line):
    m = LIST_LINE_RE.match(line)
    if not m:
        return None
    name = m.group("name").decode("utf-8", errors="replace").strip()
    return name


def repair_mojibake(name):
    """Versucht, per Latin-1->UTF-8-Rueckweg den urspruenglich korrekten
    Namen wiederherzustellen. Gibt None zurueck, wenn keine Reparatur
    noetig/moeglich ist."""
    if not _MOJIBAKE_HINT.search(name):
        return None
    try:
        repaired = name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None
    if repaired == name:
        return None
    return repaired


def quote_mailbox(name):
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped + '"'


def main():
    parser = argparse.ArgumentParser(description="Mojibake-IMAP-Ordnernamen erkennen und umbenennen")
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
    for name in all_names:
        repaired = repair_mojibake(name)
        if repaired:
            renames.append((name, repaired))

    print(f"{len(all_names)} Ordner insgesamt gefunden, {len(renames)} mit erkanntem "
          f"Mojibake-Muster.")

    if not renames:
        print("Nichts zu tun.")
        imap_conn.logout()
        return

    print("\nVorgeschlagene Umbenennungen:")
    for old, new in renames:
        print(f"  {old}  ->  {new}")

    if args.dry_run:
        print("\n(Dry-Run: nichts wurde umbenannt)")
        imap_conn.logout()
        return

    ok_count = 0
    fail_count = 0
    for old, new in renames:
        typ, resp = imap_conn.rename(quote_mailbox(old), quote_mailbox(new))
        if typ == "OK":
            print(f"  umbenannt: {old}  ->  {new}")
            ok_count += 1
        else:
            print(f"  FEHLER bei {old}: {resp}")
            fail_count += 1

    imap_conn.logout()
    print(f"\nFertig. {ok_count} umbenannt, {fail_count} fehlgeschlagen.")


if __name__ == "__main__":
    main()
