#!/usr/bin/env python3
"""
cleanup_imap_folders.py

Loescht IMAP-Ordner auf einem Postfach (z.B. Synology MailPlus), damit ein
fehlerhafter Import (z.B. durch falsch aufgespaltene Ordnernamen) komplett
neu und saubern gestartet werden kann.

WICHTIG: Das loescht Daten unwiderruflich. Erst mit --dry-run pruefen,
welche Ordner betroffen waeren!

Beispiel:
    # Erst nur anzeigen, was geloescht wuerde:
    python3 cleanup_imap_folders.py --host nas-hostname.synology.me --user benutzername --dry-run

    # Dann wirklich loeschen (INBOX bleibt standardmaessig erhalten):
    python3 cleanup_imap_folders.py --host nas-hostname.synology.me --user benutzername
"""

import argparse
import getpass
import imaplib
import re
import sys

LIST_LINE_RE = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?P<delim>"[^"]*"|NIL)\s+(?P<name>.*)$')


def parse_list_line(line):
    m = LIST_LINE_RE.match(line)
    if not m:
        return None
    name = m.group("name").decode("utf-8", errors="replace").strip()
    return name


def main():
    parser = argparse.ArgumentParser(description="IMAP-Ordner aufraeumen/loeschen")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=993)
    parser.add_argument("--user", required=True)
    parser.add_argument("--keep", nargs="*", default=["INBOX"],
                         help="Ordnernamen, die NICHT geloescht werden sollen (Standard: INBOX). "
                              "Wird ignoriert, wenn --only-from-file verwendet wird.")
    parser.add_argument("--only-from-file", default=None,
                         help="Sicherer Modus: loescht AUSSCHLIESSLICH die Ordner, die exakt in "
                              "dieser Datei stehen (eine pro Zeile, z.B. Ausgabe von "
                              "mbox_to_imap.py --list-targets). Alle anderen Ordner (auch neue, "
                              "echte Mails) bleiben unangetastet.")
    parser.add_argument("--dry-run", action="store_true", help="Nur anzeigen, nichts loeschen")
    args = parser.parse_args()

    password = getpass.getpass(f"Passwort fuer {args.user}@{args.host}: ")

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

    if args.only_from_file:
        with open(args.only_from_file, "r", encoding="utf-8") as f:
            target_names = set(line.strip() for line in f if line.strip() and not line.startswith("#"))
        # Nur Ordner loeschen, die es tatsaechlich gibt UND in der Liste stehen.
        existing_stripped = {n.strip('"'): n for n in all_names}
        to_delete = [existing_stripped[n] for n in target_names if n in existing_stripped]
        not_found = [n for n in target_names if n not in existing_stripped]
        print(f"{len(all_names)} Ordner insgesamt auf dem Server, {len(target_names)} Ordner in der "
              f"Liste, davon {len(to_delete)} tatsaechlich vorhanden und werden geloescht.")
        if not_found:
            print(f"(Hinweis: {len(not_found)} Ordner aus der Liste existieren nicht auf dem Server, "
                  f"werden uebersprungen.)")
    else:
        keep_set = set(args.keep)
        to_delete = [n for n in all_names if n.strip('"') not in keep_set]
        print(f"{len(all_names)} Ordner gefunden, {len(to_delete)} sollen geloescht werden "
              f"(behalten: {', '.join(args.keep)}).")

    # Tiefste Ordner zuerst loeschen (mehr Trennzeichen = tiefer verschachtelt),
    # damit Eltern-Ordner mit Kindern nicht vorher blockieren.
    def depth(name):
        return name.count(".") + name.count("/")

    to_delete.sort(key=depth, reverse=True)

    if args.dry_run:
        print("\nWuerde geloescht werden (Dry-Run, nichts passiert):")
        for name in to_delete:
            print(f"  {name}")
        imap_conn.logout()
        return

    ok_count = 0
    fail_count = 0
    for name in to_delete:
        try:
            imap_conn.select(name)
            imap_conn.close()
        except Exception:
            pass
        typ, resp = imap_conn.delete(name)
        if typ == "OK":
            print(f"  geloescht: {name}")
            ok_count += 1
        else:
            print(f"  FEHLER bei {name}: {resp}")
            fail_count += 1

    imap_conn.logout()
    print(f"\nFertig. {ok_count} geloescht, {fail_count} fehlgeschlagen.")
    if fail_count:
        print("Bei Fehlern: Skript einfach erneut starten (z.B. wenn Elternordner "
              "erst nach ihren Kindern geloescht werden konnten).")


if __name__ == "__main__":
    main()
