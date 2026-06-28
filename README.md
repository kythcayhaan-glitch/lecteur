# 🎵 Kyth Player

Un lecteur audio sombre et plein écran pour Windows, écrit en Python
(tkinter + VLC), pensé pour repérer et annoncer des événements dans un morceau.

## Fonctions

- ▶️ Play / Pause · ⏮️⏭️ Précédent / Suivant · ⏹ Stop
- 🔊 Réglage du volume
- 📊 Barre de progression + 〰️ forme d'onde du morceau avec curseur de lecture
- ⏱️ Compteurs **écoulé** (vert) et **restant** (rouge), en grand, millisecondes fluides
- 📜 Playlist avec, pour chaque piste, ses infos (codec, fréquence, canaux, débit, durée)
  et le **temps total** cumulé
- 🔁 Bouton boucle (lecture continue de la playlist)
- 🎯 **Repères / commentaires** : marque un instant du morceau avec un texte
  - une **alerte rouge en grand** s'affiche en haut, précédée d'un **décompte 3·2·1**,
    quand la lecture atteint le repère
  - un **bouton d'accès rapide** par repère pour sauter directement à l'instant
  - les repères sont **sauvegardés** (dans `commentaires.json`) jusqu'à suppression
- ⏩ Champ **« Aller à : »** pour sauter à un temps précis (`mm:ss`, `h:mm:ss` ou `ss`)

### Navigation dans le morceau
Le déplacement (clic sur la forme d'onde, champ « Aller à : », boutons de repères)
n'agit **qu'à l'arrêt ou en pause**, pas pendant la lecture. Le curseur peut être
placé même sur un fichier jamais lu : la position est mémorisée et appliquée au play.

### Repères
- **Simple clic** sur un fichier de la liste : affiche sa forme d'onde (sans lire)
- **Clic droit** sur la forme d'onde : ajoute un repère (ou supprime celui survolé)
- **Survol** d'un drapeau orange : affiche le texte du repère

### Raccourcis
- **Espace** : lecture / pause
- **F11** : basculer plein écran · **Échap** : quitter le plein écran

## Utilisation

### Version simple (.exe)
Télécharger `KythPlayer.exe` et double-cliquer. Aucune installation requise
(Python, VLC et ffmpeg sont embarqués dans l'exe).

### Depuis le code source
```bash
pip install python-vlc numpy imageio-ffmpeg
python lecteur.py
```
> Nécessite [VLC](https://www.videolan.org/) installé sur le PC.
> `imageio-ffmpeg` embarque ffmpeg (forme d'onde + infos/durée des fichiers),
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
