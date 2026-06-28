# -*- coding: utf-8 -*-
"""
Lecteur audio simple
--------------------
Fonctions : play/pause, volume, barre de progression (avec déplacement),
compteur de temps positif (écoulé) et négatif (restant), playlist.

Dépendances :
    pip install python-vlc
    + VLC installé sur le PC (https://www.videolan.org/)
"""

import os
import sys
import time
import tkinter as tk
from tkinter import ttk, filedialog

# Quand le programme tourne en .exe (PyInstaller), VLC est embarqué dans le
# paquet : on indique à python-vlc où trouver libvlc.dll et les plugins.
if getattr(sys, "frozen", False):
    _base = sys._MEIPASS
    os.environ["PYTHON_VLC_LIB_PATH"] = os.path.join(_base, "libvlc.dll")
    os.environ["PYTHON_VLC_MODULE_PATH"] = os.path.join(_base, "plugins")
    try:
        os.add_dll_directory(_base)
    except (AttributeError, OSError):
        pass

import vlc


def formate_temps(millisecondes, negatif=False):
    """Transforme des millisecondes en texte mm:ss.mmm (ou -mm:ss.mmm)."""
    if millisecondes < 0:
        millisecondes = 0
    millisecondes = int(millisecondes)
    secondes_totales = millisecondes // 1000
    minutes = secondes_totales // 60
    secondes = secondes_totales % 60
    millis = millisecondes % 1000
    signe = "-" if negatif else ""
    return f"{signe}{minutes:02d}:{secondes:02d}.{millis:03d}"


class LecteurAudio:
    def __init__(self, racine):
        self.racine = racine
        self.racine.title("Lecteur Audio")
        self.racine.geometry("460x420")
        self.racine.minsize(420, 380)

        # --- Moteur VLC ---
        self.instance = vlc.Instance()
        self.player = self.instance.media_player_new()

        # --- État interne ---
        self.playlist = []          # liste des chemins de fichiers
        self.index_courant = -1     # piste en cours dans la playlist
        self.utilisateur_glisse = False  # True quand on déplace la barre à la main

        # --- Interpolation du temps (pour des millisecondes fluides) ---
        # VLC ne met son chrono à jour que toutes les ~250 ms : on lit donc une
        # horloge précise entre deux mises à jour pour estimer le temps réel.
        self.dernier_temps_vlc = -1      # dernière valeur brute lue chez VLC
        self.temps_reference = 0         # temps VLC au moment de la synchro
        self.horloge_reference = None    # horloge précise au moment de la synchro

        self._construire_interface()

        # Mise à jour régulière de la barre + compteurs (toutes les 500 ms)
        self._rafraichir()

    # ------------------------------------------------------------------ #
    #  Construction de l'interface
    # ------------------------------------------------------------------ #
    def _construire_interface(self):
        # --- Playlist ---
        cadre_liste = tk.Frame(self.racine)
        cadre_liste.pack(fill="both", expand=True, padx=10, pady=(10, 5))

        self.liste = tk.Listbox(cadre_liste, activestyle="none")
        self.liste.pack(side="left", fill="both", expand=True)
        self.liste.bind("<Double-Button-1>", self._lire_selection)

        defilement = tk.Scrollbar(cadre_liste, command=self.liste.yview)
        defilement.pack(side="right", fill="y")
        self.liste.config(yscrollcommand=defilement.set)

        # --- Titre en cours ---
        self.label_titre = tk.Label(self.racine, text="Aucune piste",
                                    font=("Segoe UI", 10, "bold"))
        self.label_titre.pack(pady=(0, 4))

        # --- Barre de progression + compteurs ---
        cadre_progression = tk.Frame(self.racine)
        cadre_progression.pack(fill="x", padx=10)

        police_compteur = ("Segoe UI", 26, "bold")
        self.label_ecoule = tk.Label(cadre_progression, text="00:00", width=9,
                                     font=police_compteur)
        self.label_ecoule.pack(side="left")

        self.barre = ttk.Scale(cadre_progression, from_=0, to=1000,
                               orient="horizontal", command=self._sur_glissement)
        self.barre.pack(side="left", fill="x", expand=True, padx=6)
        # On capte le clic/relâché pour ne pas se battre avec la maj automatique
        self.barre.bind("<ButtonPress-1>", self._debut_glissement)
        self.barre.bind("<ButtonRelease-1>", self._fin_glissement)

        self.label_restant = tk.Label(cadre_progression, text="-00:00", width=9,
                                      font=police_compteur)
        self.label_restant.pack(side="left")

        # --- Boutons de contrôle ---
        cadre_boutons = tk.Frame(self.racine)
        cadre_boutons.pack(pady=8)

        tk.Button(cadre_boutons, text="⏮", width=4,
                  command=self.precedent).pack(side="left", padx=3)
        self.bouton_play = tk.Button(cadre_boutons, text="▶", width=4,
                                     command=self.play_pause)
        self.bouton_play.pack(side="left", padx=3)
        tk.Button(cadre_boutons, text="⏭", width=4,
                  command=self.suivant).pack(side="left", padx=3)
        tk.Button(cadre_boutons, text="⏹", width=4,
                  command=self.stop).pack(side="left", padx=3)

        # --- Volume ---
        cadre_volume = tk.Frame(self.racine)
        cadre_volume.pack(fill="x", padx=10, pady=(0, 6))

        tk.Label(cadre_volume, text="🔊").pack(side="left")
        self.volume = ttk.Scale(cadre_volume, from_=0, to=100,
                                orient="horizontal", command=self._sur_volume)
        self.volume.set(70)
        self.volume.pack(side="left", fill="x", expand=True, padx=6)
        self.player.audio_set_volume(70)

        # --- Ajout de fichiers ---
        tk.Button(self.racine, text="➕ Ajouter des fichiers…",
                  command=self.ajouter_fichiers).pack(pady=(0, 10))

    # ------------------------------------------------------------------ #
    #  Gestion de la playlist
    # ------------------------------------------------------------------ #
    def ajouter_fichiers(self):
        fichiers = filedialog.askopenfilenames(
            title="Choisir des fichiers audio",
            filetypes=[("Audio", "*.mp3 *.wav *.flac *.ogg *.m4a *.aac"),
                       ("Tous les fichiers", "*.*")])
        for chemin in fichiers:
            self.playlist.append(chemin)
            self.liste.insert("end", os.path.basename(chemin))

    def _lire_selection(self, evenement=None):
        selection = self.liste.curselection()
        if selection:
            self._lire_index(selection[0])

    def _lire_index(self, index):
        if 0 <= index < len(self.playlist):
            self.index_courant = index
            media = self.instance.media_new(self.playlist[index])
            self.player.set_media(media)
            self.player.play()
            self.horloge_reference = None   # nouvelle piste : repartir propre
            self.bouton_play.config(text="⏸")
            self.label_titre.config(text=os.path.basename(self.playlist[index]))
            # Surligne la piste en cours dans la liste
            self.liste.selection_clear(0, "end")
            self.liste.selection_set(index)

    # ------------------------------------------------------------------ #
    #  Contrôles de lecture
    # ------------------------------------------------------------------ #
    def play_pause(self):
        if self.index_courant == -1 and self.playlist:
            self._lire_index(0)
            return
        if self.player.is_playing():
            self.player.pause()
            self.bouton_play.config(text="▶")
        else:
            self.player.play()
            self.bouton_play.config(text="⏸")

    def stop(self):
        self.player.stop()
        self.bouton_play.config(text="▶")
        self.barre.set(0)

    def suivant(self):
        if self.playlist:
            self._lire_index((self.index_courant + 1) % len(self.playlist))

    def precedent(self):
        if self.playlist:
            self._lire_index((self.index_courant - 1) % len(self.playlist))

    # ------------------------------------------------------------------ #
    #  Volume
    # ------------------------------------------------------------------ #
    def _sur_volume(self, valeur):
        self.player.audio_set_volume(int(float(valeur)))

    # ------------------------------------------------------------------ #
    #  Barre de progression (déplacement manuel)
    # ------------------------------------------------------------------ #
    def _debut_glissement(self, evenement):
        self.utilisateur_glisse = True

    def _sur_glissement(self, valeur):
        # Pendant le glissement, on met à jour les compteurs en direct
        if self.utilisateur_glisse:
            duree = self.player.get_length()
            if duree > 0:
                position_ms = (float(valeur) / 1000) * duree
                self.label_ecoule.config(text=formate_temps(position_ms))
                self.label_restant.config(
                    text=formate_temps(duree - position_ms, negatif=True))

    def _fin_glissement(self, evenement):
        # Au relâché, on applique la nouvelle position à la lecture
        if self.player.get_length() > 0:
            self.player.set_position(float(self.barre.get()) / 1000)
        self.horloge_reference = None   # forcer une resynchro après le saut
        self.utilisateur_glisse = False

    # ------------------------------------------------------------------ #
    #  Rafraîchissement automatique
    # ------------------------------------------------------------------ #
    def _rafraichir(self):
        if not self.utilisateur_glisse and self.player.is_playing():
            duree = self.player.get_length()
            temps_vlc = self.player.get_time()  # chrono brut VLC (imprécis)

            # Quand VLC publie une nouvelle valeur, on resynchronise notre
            # horloge précise dessus ; entre deux, on interpole.
            if temps_vlc != self.dernier_temps_vlc:
                self.dernier_temps_vlc = temps_vlc
                self.temps_reference = temps_vlc
                self.horloge_reference = time.perf_counter()

            if self.horloge_reference is not None:
                ecoule = (time.perf_counter() - self.horloge_reference) * 1000
                position = self.temps_reference + ecoule
            else:
                position = temps_vlc

            if duree > 0:
                position = min(position, duree)  # ne pas dépasser la fin
                self.barre.set((position / duree) * 1000)
                self.label_ecoule.config(text=formate_temps(position))
                self.label_restant.config(
                    text=formate_temps(duree - position, negatif=True))
        elif not self.player.is_playing():
            # En pause/arrêt : on coupe l'interpolation pour repartir propre.
            self.horloge_reference = None

        # Passe à la piste suivante quand le morceau est terminé
        if self.player.get_state() == vlc.State.Ended:
            self.suivant()

        self.racine.after(16, self._rafraichir)


if __name__ == "__main__":
    racine = tk.Tk()
    app = LecteurAudio(racine)
    racine.mainloop()
