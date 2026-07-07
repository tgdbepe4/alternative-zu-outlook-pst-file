# Alternative zu Outlook PST File

Sammlung von Python-Skripten zur Migration von Outlook-PST-Archiven auf einen
selbstgehosteten IMAP-Server (z.B. Synology MailPlus Server), als Alternative
zu lokalen, clientgebundenen PST-Dateien.

## Hintergrund

PST-Dateien sind an Windows Outlook gebunden, werden von neueren Outlook-
Versionen nur noch eingeschränkt unterstützt (kein Live-Mounting, nur
Import/Export) und sind auf einem einzelnen Gerät gefangen. Diese Skripte
lösen das, indem sie den Inhalt einer PST-Datei sauber auf ein reguläres
IMAP-Postfach übertragen – von dort aus sind die Mails von jedem Gerät und
jedem Mail-Client aus zugänglich, wie ein "normales" E-Mail-Konto.

## Workflow

1. **PST → mbox** (mit `readpst`, Teil von `libpst`, macOS: `brew install libpst`):
   ```bash
   readpst -r -o ~/pst-export/ archiv.pst
   ```
2. **mbox → IMAP** (dieses Repo):
   ```bash
   python3 mbox_to_imap.py --source ~/pst-export/archiv --host mail.example.com --user benutzername --dry-run
   python3 mbox_to_imap.py --source ~/pst-export/archiv --host mail.example.com --user benutzername
   ```

## Skripte

| Skript | Zweck |
|---|---|
| `mbox_to_imap.py` | Hauptimport: überträgt mbox-Ordner (aus `readpst -r`) per IMAP APPEND auf einen Zielserver. Unterstützt `--dry-run`, `--list-targets`, `--prefix` (gemeinsamer Überordner) und ist bei Abbruch fortsetzbar (Fortschrittsdatei). |
| `cleanup_imap_folders.py` | Löscht IMAP-Ordner gezielt (`--only-from-file`, sicher) oder pauschal (`--keep`, mit Vorsicht zu verwenden). |
| `rename_mojibake_folders.py` | Diagnose: erkennt Latin-1-Fehlinterpretationen von UTF-8 in Ordnernamen. |
| `fix_imap_folder_names.py` | Repariert echte Modified-UTF-7-kodierte Ordnernamen per `RENAME`, ohne Nachrichten zu verändern. |
| `mark_all_read.py` | Markiert alle (oder nur ungelesene, per `--dry-run` geprüft) Nachrichten in allen Ordnern als gelesen. |
| `list_empty_folders.py` | Listet leere Ordner, reine Container-Ordner (`\Noselect`) und komplett leere Ordner-Zweige auf. |

## Wichtige Erkenntnisse (siehe Code-Kommentare für Details)

- **Modified UTF-7 ist bei vielen Dovecot-basierten Servern (z.B. MailPlus) zwingend** für Ordnernamen mit Sonderzeichen – rohes UTF-8 wird mit `"not valid mUTF-7"` abgelehnt.
- **Der Punkt (`.`) ist bei vielen Servern das Ordner-Trennzeichen** – Ordnernamen mit Punkt (z.B. Domainnamen wie `firma.ch`) werden sonst fälschlich in Unterordner aufgespalten. Fix: Punkte im Ordnernamen selbst durch Bindestriche ersetzen.
- **Windows Outlook 365 hat einen bekannten Zeichensatz-Bug** (Stand 2026): Umlaute/Ordnernamen erscheinen verstümmelt, weil Outlook fälschlich auf ISO-8859-1 statt UTF-8 zurückfällt. Fix: Datei → Optionen → Erweitert → Internationale Optionen → Unicode (UTF-8).
- Outlooks eingebautes Drag & Drop von PST-Ordnern in ein IMAP-Konto ist bei grossen Mengen unzuverlässig (asynchroner Hintergrund-Upload, kein verlässlicher Fortschritt/Fehlerbericht) – deshalb der skriptbasierte Ansatz.

## Voraussetzungen

- Python 3
- `readpst` (Paket `libpst`) für den PST→mbox-Schritt
- Ein IMAP-Konto mit Schreibzugriff auf dem Zielserver

## Sicherheit

Passwörter werden ausschliesslich interaktiv abgefragt (`getpass`) und nie
in Dateien, Logs oder der Kommandozeilen-History gespeichert.
