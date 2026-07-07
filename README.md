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

Outlooks eingebautes Drag & Drop von PST-Ordnern in ein IMAP-Konto ist bei
grossen Mengen unzuverlässig: Der Upload läuft asynchron im Hintergrund,
bricht bei Tausenden Nachrichten oft still ab, ohne klare Fehlermeldung, und
lässt sich nicht zuverlässig fortsetzen. Diese Skripte umgehen das komplett.

## Workflow

1. **PST → mbox** (mit `readpst`, Teil von `libpst`, macOS: `brew install libpst`):
   ```bash
   readpst -r -o ~/pst-export/ archiv.pst
   ```
   Erzeugt pro Outlook-Ordner eine `mbox`-Datei in einer Verzeichnisstruktur,
   die dem ursprünglichen Ordnerbaum entspricht.

2. **mbox → IMAP** (dieses Repo):
   ```bash
   # Erst zaehlen, nichts importieren:
   python3 mbox_to_imap.py --source ~/pst-export/archiv --host mail.example.com --user benutzername --dry-run

   # Dann echt importieren:
   python3 mbox_to_imap.py --source ~/pst-export/archiv --host mail.example.com --user benutzername
   ```

## Skripte

| Skript | Zweck |
|---|---|
| `mbox_to_imap.py` | Hauptimport: überträgt mbox-Ordner (aus `readpst -r`) per IMAP APPEND auf einen Zielserver. |
| `cleanup_imap_folders.py` | Löscht IMAP-Ordner gezielt (`--only-from-file`, sicher) oder pauschal (`--keep`, mit Vorsicht zu verwenden). |
| `rename_mojibake_folders.py` | Diagnose: erkennt Latin-1-Fehlinterpretationen von UTF-8 in Ordnernamen. |
| `fix_imap_folder_names.py` | Repariert echte Modified-UTF-7-kodierte Ordnernamen per `RENAME`, ohne Nachrichten zu verändern. |
| `mark_all_read.py` | Markiert alle Nachrichten in allen Ordnern als gelesen. |
| `list_empty_folders.py` | Listet leere Ordner, reine Container-Ordner (`\Noselect`) und komplett leere Ordner-Zweige auf. |

## mbox_to_imap.py – Optionen-Referenz

| Option | Beschreibung |
|---|---|
| `--source <pfad>` | Wurzelverzeichnis mit den mbox-Ordnern (Ausgabe von `readpst -r`). Pflicht. |
| `--host <host>` | IMAP-Server-Hostname. Pflicht. |
| `--port <port>` | IMAP-Port. Standard: `993`. |
| `--user <name>` | IMAP-Benutzername. Pflicht. Passwort wird interaktiv abgefragt (nie als Argument übergeben, landet nicht in der Shell-History). |
| `--dry-run` | Zählt nur die Nachrichten pro Quellordner, importiert nichts. |
| `--list-targets` | Verbindet sich nur lesend (nur für das Server-Trennzeichen), erstellt/ändert nichts, gibt aber die Ziel-IMAP-Ordnernamen aus, die dieser Lauf verwenden würde. Praktisch zur Kontrolle vor dem Import oder als Eingabe für `cleanup_imap_folders.py --only-from-file`. |
| `--prefix <name>` | Bündelt **alle** importierten Ordner unter einem gemeinsamen IMAP-Überordner, statt sie auf oberster Ebene anzulegen. Beispiel: `--prefix "Archiv"` macht aus dem Quellordner `Projekt/Kunde` den IMAP-Ordner `Archiv.Projekt.Kunde` statt `Projekt.Kunde`. Nützlich, um mehrere Importläufe (z.B. verschiedene PST-Dateien) sauber gebündelt und getrennt von aktuellen, laufenden Mails abzulegen. |

### Fortschrittsdatei

Das Skript legt im `--source`-Verzeichnis automatisch eine versteckte Datei
namens `.mbox_to_imap_progress.txt` an. Darin steht pro Zeile ein bereits
vollständig und fehlerfrei importierter Unterordner; bei einem erneuten
Lauf werden diese übersprungen – dadurch ist der Import nach einem Abbruch
(Verbindungsabbruch, Rechner-Standby etc.) einfach durch erneutes Ausführen
desselben Befehls fortsetzbar.

**Soll der Import für ein Quellverzeichnis komplett neu beginnen** (z.B.
nach einer Korrektur oder einem Server-seitigen Cleanup), muss diese Datei
gelöscht werden:
```bash
rm ~/pst-export/<name-des-exportordners>/.mbox_to_imap_progress.txt
```
Da der Dateiname mit einem Punkt beginnt, zeigt `ls` sie nicht standardmässig
an – zur Kontrolle: `ls -la ~/pst-export/<name>/ | grep progress`.

## Troubleshooting / bekannte Stolpersteine

- **`UnicodeEncodeError` beim Login:** Passwörter mit Umlauten scheitern an
  Pythons Standard-ASCII-Encoding in `imaplib`. Das Skript setzt
  `imap_conn._encoding = "utf-8"`, um das zu umgehen.
- **`UnicodeDecodeError` beim Lesen der mbox-Datei:** Pythons eingebaute
  `mailbox.mbox`-Klasse dekodiert die interne `"From "`-Trennzeile strikt als
  ASCII und stürzt bei Umlauten im Absendernamen ab. Das Skript liest mbox-
  Dateien deshalb selbst roh als Bytes ein, statt die Standardbibliothek zu
  nutzen.
- **`"8bit data in atom"`-Fehler:** IMAP-Ordnernamen mit Sonderzeichen
  müssen nach "Modified UTF-7" kodiert und in Anführungszeichen gesetzt
  werden – das übernimmt `imaplib` nicht automatisch. Wird im Skript korrekt
  gehandhabt.
- **`"Mailbox name is not valid mUTF-7"`:** Manche Dovecot-basierte Server
  (u.a. Synology MailPlus) lehnen rohes UTF-8 in Ordnernamen ab und
  verlangen zwingend eine gültige Modified-UTF-7-Kodierung. Das Skript
  kodiert deshalb immer nach Modified UTF-7, nicht nach rohem UTF-8.
- **Ordnernamen mit Punkt werden ungewollt aufgespalten:** Der Punkt (`.`)
  ist bei vielen Servern das Ordner-Trennzeichen. Ein Ordnername wie
  `firma.ch` würde sonst fälschlich in zwei Unterordner `firma` → `ch`
  aufgeteilt. Das Skript ersetzt Punkte innerhalb eines Ordnernamens
  automatisch durch Bindestriche (`firma-ch`).
- **Verstümmelte Umlaute NUR in Windows Outlook 365 (Classic), obwohl
  Mac Mail/Webmail korrekt anzeigen:** Kein Daten- oder Serverproblem,
  sondern ein bekannter Outlook-Zeichensatz-Bug (Stand 2026): Outlook fällt
  fälschlich auf ISO-8859-1 statt UTF-8 zurück. Fix in Outlook: Datei →
  Optionen → Erweitert → Internationale Optionen → Zeichensatz auf
  **Unicode (UTF-8)** umstellen.
- **Outlook zeigt nach dem Import nicht alle Ordner an:** Klassisches
  IMAP-Subscription-Problem – per `CREATE` neu angelegte Ordner sind auf dem
  Server vorhanden, aber nicht automatisch "abonniert". Fix: In Outlook
  Kontoeinstellungen → Konto bearbeiten → **"IMAP-Ordner..."** → Abfragen →
  alle abonnieren.
- **Grosse Mengen per Outlook-Drag-&-Drop unvollständig übertragen:**
  Outlooks Hintergrund-Upload bricht bei mehreren Tausend Nachrichten oft
  still ab. Mit `list_empty_folders.py` lässt sich das aufdecken (Ordner, die
  laut Quelle Nachrichten enthalten sollten, aber am Server leer sind).
  Fix: betroffene Ordner am Server löschen, sauber per `mbox_to_imap.py`
  neu importieren.

## Voraussetzungen

- Python 3
- `readpst` (Paket `libpst`) für den PST→mbox-Schritt
- Ein IMAP-Konto mit Schreibzugriff auf dem Zielserver

## Sicherheit

Passwörter werden ausschliesslich interaktiv abgefragt (`getpass`) und nie
in Dateien, Logs oder der Kommandozeilen-History gespeichert.
