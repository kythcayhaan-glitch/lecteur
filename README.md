# 🎵 Lecteur Audio

Un lecteur audio simple et léger pour Windows, écrit en Python (tkinter + VLC).

## Fonctions

- ▶️ Play / Pause
- 🔊 Réglage du volume
- 📊 Barre de progression avec déplacement (avance/recul dans le morceau)
- ⏱️ Compteur de temps **positif** (écoulé) et **négatif** (restant), avec millisecondes fluides
- 📜 Playlist (passage automatique à la piste suivante)
- ⏮️⏭️ Précédent / Suivant

## Utilisation

### Version simple (.exe)
Télécharger `LecteurAudio.exe` et double-cliquer. Aucune installation requise
(Python et VLC sont embarqués dans l'exe).

### Depuis le code source
```bash
pip install python-vlc
python lecteur.py
```
> Nécessite [VLC](https://www.videolan.org/) installé sur le PC.

## Recompiler l'exe

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "LecteurAudio" ^
  --add-binary "C:\Program Files\VideoLAN\VLC\libvlc.dll;." ^
  --add-binary "C:\Program Files\VideoLAN\VLC\libvlccore.dll;." ^
  --add-data "C:\Program Files\VideoLAN\VLC\plugins;plugins" ^
  lecteur.py
```
