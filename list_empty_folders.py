#!/usr/bin/env python3
"""
list_empty_folders.py

Listet alle IMAP-Ordner auf, die 0 Nachrichten enthalten. Reine Container-
Ordner (\\Noselect, nur Struktur ohne eigene Mails) werden separat
ausgewiesen, nicht als "leer" gezaehlt -- das sind zwei unterschiedliche
Dinge.

Aendert nichts, loescht nichts -- rein informativ.

Beispiel:
    python3 list_empty_folders.py --host bruwi62.synology.me --user tgdbepe4
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
    return decode_imap_utf7(unquote_mailbox(folder))


def get_delimiter(imap_conn):
    typ, data = imap_conn.list()
    if typ == "OK" and data and data[0]:
        entry = data[0].decode(errors="replace")
        try:
            delim = entry.split('"')[1]
            if delim:
                return delim
        except IndexError:
            pass
    return "."


def find_empty_branches(folder_counts, delimiter):
    """folder_counts: dict {roher_ordnername: anzahl_nachrichten_oder_None}.
    Gibt die Liste der 'Wurzeln' komplett leerer Zweige zurueck (Ordner,
    bei denen weder der Ordner selbst noch irgendein Unterordner
    Nachrichten enthaelt), ohne bereits erfasste Unterordner doppelt
    aufzufuehren."""
    plain_names = {f: unquote_mailbox(f) for f in folder_counts}
    own_count = {f: (folder_counts[f] or 0) for f in folder_counts}

    def parent_of(f):
        parts = plain_names[f].split(delimiter)
        if len(parts) <= 1:
            return None
        parent_plain = delimiter.join(parts[:-1])
        for other in folder_counts:
            if plain_names[other] == parent_plain:
                return other
        return None

    children = {f: [] for f in folder_counts}
    parent_map = {}
    for f in folder_counts:
        p = parent_of(f)
        parent_map[f] = p
        if p:
            children[p].append(f)

    subtree_total = {}

    def compute(f):
        if f in subtree_total:
            return subtree_total[f]
        total = own_count[f] + sum(compute(c) for c in children[f])
        subtree_total[f] = total
        return total

    for f in folder_counts:
        compute(f)

    roots = []
    for f in folder_counts:
        if subtree_total[f] != 0:
            continue
        p = parent_map[f]
        if p and subtree_total[p] == 0:
            continue  # bereits durch den Elternordner abgedeckt
        roots.append(f)
    return roots


def main():
    parser = argparse.ArgumentParser(description="Leere IMAP-Ordner auflisten")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=993)
    parser.add_argument("--user", required=True)
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

    delimiter = get_delimiter(imap_conn)

    folders = [parse_list_line(line) for line in data]
    folders = [f for f in folders if f]

    print(f"{len(folders)} Ordner gefunden.\n")

    empty_folders = []
    container_folders = []
    folder_counts = {}

    for folder in folders:
        typ, resp = imap_conn.select(folder, readonly=True)
        if typ != "OK":
            container_folders.append(folder)
            folder_counts[folder] = None
            continue
        # resp[0] enthaelt die Nachrichtenanzahl als Bytes, z.B. b'0'
        count = int(resp[0])
        imap_conn.close()
        folder_counts[folder] = count
        if count == 0:
            empty_folders.append(folder)

    imap_conn.logout()

    print(f"=== {len(empty_folders)} leere Ordner (0 Nachrichten, aber auswaehlbar) ===")
    for f in empty_folders:
        print(f"  {display_name(f)}")

    print(f"\n=== {len(container_folders)} reine Container-Ordner (\\Noselect, nicht auswaehlbar) ===")
    for f in container_folders:
        print(f"  {display_name(f)}")

    empty_branch_roots = find_empty_branches(folder_counts, delimiter)
    print(f"\n=== {len(empty_branch_roots)} komplett leere Zweige (Ordner UND alle seine "
          f"Unterordner zusammen ohne jede Nachricht -- ganze Zweige, die man bedenkenlos "
          f"loeschen koennte) ===")
    for f in empty_branch_roots:
        print(f"  {display_name(f)}")

    print(f"\nGesamt: {len(folders)} Ordner, davon {len(empty_folders)} leer, "
          f"{len(container_folders)} reine Container, {len(empty_branch_roots)} komplett "
          f"leere Zweige (Wurzeln).")


if __name__ == "__main__":
    main()
