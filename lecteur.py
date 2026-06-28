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

import subprocess
import threading
import numpy as np
import imageio_ffmpeg


# --- Palette de couleurs (thème sombre) ---
FOND = "#000000"        # noir : fond général du lecteur
FOND_CLAIR = "#1a1a1a"  # gris très foncé : listes / boutons
TEXTE = "#e0e0e0"       # gris clair : textes neutres
VERT = "#00e676"        # compteur écoulé (positif)
ROUGE = "#ff5252"       # compteur restant (négatif)


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
        self.racine.geometry("460x580")
        self.racine.minsize(420, 520)
        self.racine.configure(bg=FOND)

        # --- Moteur VLC ---
        self.instance = vlc.Instance()
        self.player = self.instance.media_player_new()

        # --- État interne ---
        self.playlist = []          # liste des chemins de fichiers
        self.index_courant = -1     # piste en cours dans la playlist
        self.utilisateur_glisse = False  # True quand on déplace la barre à la main

        # --- Forme d'onde (waveform) ---
        # On décode chaque piste en arrière-plan avec ffmpeg pour obtenir une
        # courbe d'amplitude. token_onde sert à ignorer un décodage périmé
        # (si l'utilisateur change de piste avant la fin du calcul).
        self.forme_onde = None      # tableau numpy des crêtes (0..1) ou None
        self.token_onde = 0         # jeton de la piste en cours de décodage
        self.ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

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
        # Style commun aux boutons sombres
        style_bouton = dict(bg=FOND_CLAIR, fg=TEXTE, activebackground="#333333",
                            activeforeground=TEXTE, relief="flat",
                            borderwidth=0, highlightthickness=0,
                            font=("Segoe UI", 18), padx=8, pady=6)

        # --- Playlist ---
        cadre_liste = tk.Frame(self.racine, bg=FOND)
        cadre_liste.pack(fill="both", expand=True, padx=10, pady=(10, 5))

        self.liste = tk.Listbox(cadre_liste, activestyle="none",
                                bg=FOND_CLAIR, fg=TEXTE,
                                selectbackground=VERT, selectforeground=FOND,
                                highlightthickness=0, borderwidth=0)
        self.liste.pack(side="left", fill="both", expand=True)
        self.liste.bind("<Double-Button-1>", self._lire_selection)

        defilement = tk.Scrollbar(cadre_liste, command=self.liste.yview)
        defilement.pack(side="right", fill="y")
        self.liste.config(yscrollcommand=defilement.set)

        # --- Titre en cours ---
        self.label_titre = tk.Label(self.racine, text="Aucune piste",
                                    font=("Segoe UI", 10, "bold"),
                                    bg=FOND, fg=TEXTE)
        self.label_titre.pack(pady=(0, 4))

        # --- Forme d'onde (cliquable pour se déplacer) ---
        self.canvas_onde = tk.Canvas(self.racine, height=90, bg=FOND_CLAIR,
                                     highlightthickness=0)
        self.canvas_onde.pack(fill="x", padx=10, pady=(0, 6))
        self.canvas_onde.bind("<Button-1>", self._clic_onde)
        self.canvas_onde.bind("<Configure>", lambda e: self._dessiner_onde())

        # --- Compteurs empilés : écoulé (vert) au-dessus, restant (rouge) en dessous ---
        cadre_compteurs = tk.Frame(self.racine, bg=FOND)
        cadre_compteurs.pack(fill="x", padx=10)

        self.label_ecoule = tk.Label(cadre_compteurs, text="00:00.000",
                                     font=("Segoe UI", 34, "bold"),
                                     bg=FOND, fg=VERT)
        self.label_ecoule.pack()

        self.label_restant = tk.Label(cadre_compteurs, text="-00:00.000",
                                      font=("Segoe UI", 34, "bold"),
                                      bg=FOND, fg=ROUGE)
        self.label_restant.pack()

        # --- Barre de progression (pleine largeur, sous les compteurs) ---
        cadre_progression = tk.Frame(self.racine, bg=FOND)
        cadre_progression.pack(fill="x", padx=10, pady=(2, 0))

        self.barre = ttk.Scale(cadre_progression, from_=0, to=1000,
                               orient="horizontal", command=self._sur_glissement)
        self.barre.pack(fill="x", expand=True)
        # On capte le clic/relâché pour ne pas se battre avec la maj automatique
        self.barre.bind("<ButtonPress-1>", self._debut_glissement)
        self.barre.bind("<ButtonRelease-1>", self._fin_glissement)

        # --- Boutons de contrôle ---
        cadre_boutons = tk.Frame(self.racine, bg=FOND)
        cadre_boutons.pack(pady=8)

        tk.Button(cadre_boutons, text="⏮", width=4,
                  command=self.precedent, **style_bouton).pack(side="left", padx=3)
        self.bouton_play = tk.Button(cadre_boutons, text="▶", width=4,
                                     command=self.play_pause, **style_bouton)
        self.bouton_play.pack(side="left", padx=3)
        tk.Button(cadre_boutons, text="⏭", width=4,
                  command=self.suivant, **style_bouton).pack(side="left", padx=3)
        tk.Button(cadre_boutons, text="⏹", width=4,
                  command=self.stop, **style_bouton).pack(side="left", padx=3)

        # Bouton bascule "boucle" : enfoncé = playlist en boucle continue
        self.boucle = tk.BooleanVar(value=False)
        tk.Checkbutton(cadre_boutons, text="🔁", width=4, variable=self.boucle,
                       indicatoron=False, bg=FOND_CLAIR, fg=TEXTE,
                       selectcolor=VERT, activebackground="#333333",
                       activeforeground=TEXTE, relief="flat",
                       borderwidth=0, highlightthickness=0, offrelief="flat",
                       font=("Segoe UI", 18), padx=8,
                       pady=6).pack(side="left", padx=3)

        # --- Volume ---
        cadre_volume = tk.Frame(self.racine, bg=FOND)
        cadre_volume.pack(fill="x", padx=10, pady=(0, 6))

        tk.Label(cadre_volume, text="🔊", bg=FOND, fg=TEXTE).pack(side="left")
        self.volume = ttk.Scale(cadre_volume, from_=0, to=100,
                                orient="horizontal", command=self._sur_volume)
        self.volume.set(70)
        self.volume.pack(side="left", fill="x", expand=True, padx=6)
        self.player.audio_set_volume(70)

        # --- Ajout de fichiers ---
        tk.Button(self.racine, text="➕ Ajouter des fichiers…",
                  command=self.ajouter_fichiers,
                  **style_bouton).pack(pady=(0, 10))

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

    def _lire_index(self, index, lire=True):
        if 0 <= index < len(self.playlist):
            self.index_courant = index
            media = self.instance.media_new(self.playlist[index])
            self.player.set_media(media)
            self.horloge_reference = None   # nouvelle piste : repartir propre
            self.label_titre.config(text=os.path.basename(self.playlist[index]))
            # Surligne la piste en cours dans la liste
            self.liste.selection_clear(0, "end")
            self.liste.selection_set(index)
            # Lance le calcul de la forme d'onde sans bloquer l'interface
            self._lancer_forme_onde(self.playlist[index])
            if lire:
                self.player.play()
                self.bouton_play.config(text="⏸")
            else:
                # On charge la piste mais on attend : lecture à l'arrêt
                self.bouton_play.config(text="▶")
                self.barre.set(0)
                self.label_ecoule.config(text="00:00.000")
                self.label_restant.config(text="-00:00.000")

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
    #  Forme d'onde (waveform)
    # ------------------------------------------------------------------ #
    def _lancer_forme_onde(self, chemin):
        """Vide la courbe et démarre le décodage de la piste en arrière-plan."""
        self.forme_onde = None
        self.canvas_onde.delete("all")
        self.token_onde += 1
        token = self.token_onde
        threading.Thread(target=self._decoder_forme_onde,
                         args=(chemin, token), daemon=True).start()

    def _decoder_forme_onde(self, chemin, token, resolution=1200):
        """Décode l'audio en mono via ffmpeg et calcule l'enveloppe d'amplitude.

        Tourne dans un thread : à la fin, le tracé est replanifié sur le thread
        principal de tkinter (seul autorisé à toucher aux widgets).
        """
        try:
            # ffmpeg -> PCM 16 bits, mono, 8 kHz, écrit sur la sortie standard.
            commande = [self.ffmpeg, "-v", "quiet", "-i", chemin,
                        "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
            brut = subprocess.run(
                commande, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            echantillons = np.frombuffer(brut, dtype=np.int16)
            if echantillons.size == 0:
                return
            amplitudes = np.abs(echantillons.astype(np.float32))
            # On regroupe en `resolution` colonnes en prenant la crête de chacune
            taille = (amplitudes.size // resolution) * resolution
            if taille == 0:
                cretes = amplitudes
            else:
                cretes = amplitudes[:taille].reshape(resolution, -1).max(axis=1)
            maxi = cretes.max()
            cretes = cretes / maxi if maxi > 0 else cretes
        except Exception:
            return
        # Le décodage peut être long : si l'utilisateur a changé de piste
        # entre-temps, ce résultat est périmé -> on l'ignore.
        if token == self.token_onde:
            self.racine.after(0, lambda: self._recevoir_forme_onde(cretes, token))

    def _recevoir_forme_onde(self, cretes, token):
        if token == self.token_onde:
            self.forme_onde = cretes
            self._dessiner_onde()

    def _dessiner_onde(self):
        """Trace les barres d'amplitude (statiques) sur le canvas."""
        c = self.canvas_onde
        c.delete("all")
        cretes = self.forme_onde
        largeur = c.winfo_width()
        hauteur = c.winfo_height()
        if cretes is None or largeur <= 1:
            return
        milieu = hauteur / 2
        n = len(cretes)
        for x in range(largeur):
            amp = cretes[int(x / largeur * n)] * (milieu - 2)
            c.create_line(x, milieu - amp, x, milieu + amp,
                          fill=VERT, tags="onde")

    def _maj_curseur_onde(self, position, duree):
        """Déplace le trait vertical de lecture sur la forme d'onde."""
        c = self.canvas_onde
        c.delete("curseur")
        if self.forme_onde is None or duree <= 0:
            return
        x = (position / duree) * c.winfo_width()
        c.create_line(x, 0, x, c.winfo_height(),
                      fill="#ffffff", width=2, tags="curseur")

    def _clic_onde(self, evenement):
        """Cliquer sur la forme d'onde déplace la lecture à cet endroit."""
        largeur = self.canvas_onde.winfo_width()
        if largeur > 0 and self.player.get_length() > 0:
            fraction = max(0.0, min(1.0, evenement.x / largeur))
            self.player.set_position(fraction)
            self.barre.set(fraction * 1000)
            self.horloge_reference = None

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
                self._maj_curseur_onde(position, duree)
        elif not self.player.is_playing():
            # En pause/arrêt : on coupe l'interpolation pour repartir propre.
            self.horloge_reference = None

        # Fin de morceau : on passe à la piste suivante.
        #   - boucle cochée   -> on la lance (lecture continue en boucle)
        #   - boucle décochée -> on la charge sans la lancer (lecture arrêtée)
        if self.player.get_state() == vlc.State.Ended and self.playlist:
            suivant = (self.index_courant + 1) % len(self.playlist)
            self._lire_index(suivant, lire=self.boucle.get())

        self.racine.after(16, self._rafraichir)


if __name__ == "__main__":
    racine = tk.Tk()
    app = LecteurAudio(racine)
    racine.mainloop()
