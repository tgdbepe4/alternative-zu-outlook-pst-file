#!/usr/bin/env python3
"""
mark_all_read.py

Markiert ALLE Nachrichten in ALLEN Ordnern eines IMAP-Postfachs als gelesen
(setzt das \\Seen-Flag). Nuetzlich nach einem Massenimport, bei dem alle
Nachrichten als "ungelesen" markiert wurden.

Aendert nur den Gelesen-Status, loescht/verschiebt/veraendert nichts an den
Nachrichten selbst.

Beispiel:
    # Erst nur anzeigen, wie viele Nachrichten betroffen waeren:
    python3 mark_all_read.py --host nas-hostname.synology.me --user benutzername --dry-run

    # Dann wirklich markieren:
    python3 mark_all_read.py --host nas-hostname.synology.me --user benutzername
"""

import argparse
import base64
import getpass
import imaplib
import re
import sys

LIST_LINE_RE = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?P<delim>"[^"]*"|NIL)\s+(?P<name>.*)$')


def parse_list_line(line):
    m = LIST_LINE_RE.match(line)
    if not m:
        return None
    return m.group("name").decode("utf-8", errors="replace").strip()


def unquote_mailbox(raw):
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        inner = raw[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\")
    return raw


def decode_imap_utf7(s):
    """Dekodiert 'Modified UTF-7' (RFC 3501) nur fuer eine lesbare Anzeige
    im Terminal -- der eigentliche IMAP-Befehl verwendet weiterhin den
    rohen Servernamen, das hier ist rein kosmetisch."""
    if "&" not in s:
        return s
    result = []
    i, n = 0, len(s)
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
                    result.append(s[i:j + 1])
            i = j + 1
        else:
            result.append(c)
            i += 1
    return "".join(result)


def display_name(folder):
    """Lesbarer Name nur fuer Anzeigezwecke."""
    return decode_imap_utf7(unquote_mailbox(folder))


def main():
    parser = argparse.ArgumentParser(description="Alle Nachrichten in allen IMAP-Ordnern als gelesen markieren")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=993)
    parser.add_argument("--user", required=True)
    parser.add_argument("--dry-run", action="store_true", help="Nur zaehlen, nichts markieren")
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

    folders = [parse_list_line(line) for line in data]
    folders = [f for f in folders if f]

    print(f"{len(folders)} Ordner gefunden.\n")

    total_unseen = 0
    total_marked = 0
    total_failed = 0

    for folder in folders:
        typ, _ = imap_conn.select(folder, readonly=args.dry_run)
        if typ != "OK":
            print(f"  Konnte Ordner nicht oeffnen (vermutlich reiner Container-Ordner ohne eigene Nachrichten): {display_name(folder)}")
            total_failed += 1
            continue

        typ, unseen_data = imap_conn.search(None, "UNSEEN")
        if typ != "OK":
            print(f"  Konnte ungelesene Nachrichten nicht ermitteln: {display_name(folder)}")
            imap_conn.close()
            total_failed += 1
            continue

        unseen_ids = unseen_data[0].split()
        count = len(unseen_ids)
        if count == 0:
            imap_conn.close()
            continue

        total_unseen += count
        print(f"  {display_name(folder)}: {count} ungelesen", end="")

        if args.dry_run:
            print("  (Dry-Run, nichts geaendert)")
            imap_conn.close()
            continue

        typ, resp = imap_conn.store("1:*", "+FLAGS", "\\Seen")
        if typ == "OK":
            print("  -> als gelesen markiert")
            total_marked += count
        else:
            print(f"  -> FEHLER: {resp}")
            total_failed += 1

        imap_conn.close()

    imap_conn.logout()
    print("")
    if args.dry_run:
        print(f"Gesamt: {total_unseen} ungelesene Nachrichten gefunden (Dry-Run, nichts geaendert).")
    else:
        print(f"Fertig. {total_marked} Nachrichten als gelesen markiert, {total_failed} Ordner mit Fehlern.")


if __name__ == "__main__":
    main()
