# 🎵 Kyth Player

Un lecteur audio simple et léger pour Windows, écrit en Python (tkinter + VLC).

## Fonctions

- ▶️ Play / Pause
- 🔊 Réglage du volume
- 📊 Barre de progression avec déplacement (avance/recul dans le morceau)
- 〰️ Forme d'onde du morceau (cliquable pour se déplacer) avec curseur de lecture
- ⏱️ Compteur de temps **positif** (écoulé) et **négatif** (restant), avec millisecondes fluides
- 📜 Playlist (passage automatique à la piste suivante)
- 🔁 Bouton boucle (lecture continue de la playlist)
- ⏮️⏭️ Précédent / Suivant

## Utilisation

### Version simple (.exe)
Télécharger `KythPlayer.exe` et double-cliquer. Aucune installation requise
(Python et VLC sont embarqués dans l'exe).

### Depuis le code source
```bash
pip install python-vlc numpy imageio-ffmpeg
python lecteur.py
```
> Nécessite [VLC](https://www.videolan.org/) installé sur le PC.
> `imageio-ffmpeg` embarque ffmpeg (utilisé pour calculer la forme d'onde),
> aucune installation séparée de ffmpeg n'est nécessaire.

## Recompiler l'exe

```bash
pip install pyinstaller python-vlc numpy imageio-ffmpeg
pyinstaller --onefile --windowed --name "KythPlayer" ^
  --add-binary "C:\Program Files\VideoLAN\VLC\libvlc.dll;." ^
  --add-binary "C:\Program Files\VideoLAN\VLC\libvlccore.dll;." ^
  --add-data "C:\Program Files\VideoLAN\VLC\plugins;plugins" ^
  --collect-all imageio_ffmpeg ^
  lecteur.py
```

> `--collect-all imageio_ffmpeg` embarque le binaire ffmpeg (forme d'onde) dans
> l'exe ; numpy est détecté automatiquement par PyInstaller. La ligne avec `^`
> est la continuation de commande sous **cmd**. Sous **PowerShell**, remplace
> les `^` en fin de ligne par des backticks `` ` ``.
