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
import json
import time
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox

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

import re
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
        self.racine.title("Kyth Player")
        self.racine.geometry("460x660")
        self.racine.minsize(420, 600)
        self.racine.configure(bg=FOND)

        # Démarrage en plein écran ; Échap pour quitter, F11 pour basculer
        self.racine.attributes("-fullscreen", True)
        self.racine.bind(
            "<Escape>", lambda e: self.racine.attributes("-fullscreen", False))
        self.racine.bind("<F11>", lambda e: self.racine.attributes(
            "-fullscreen", not self.racine.attributes("-fullscreen")))
        # Barre espace = lecture / pause
        self.racine.bind("<space>", self._touche_espace)

        # --- Moteur VLC ---
        self.instance = vlc.Instance()
        self.player = self.instance.media_player_new()

        # --- État interne ---
        self.playlist = []          # liste des chemins de fichiers
        self.durees = []            # durée (ms) de chaque piste (alignée sur playlist)
        self.index_courant = -1     # piste en cours dans la playlist
        self.utilisateur_glisse = False  # True quand on déplace la barre à la main

        # --- Forme d'onde (waveform) ---
        # On décode chaque piste en arrière-plan avec ffmpeg pour obtenir une
        # courbe d'amplitude. token_onde sert à ignorer un décodage périmé
        # (si l'utilisateur change de piste avant la fin du calcul).
        self.forme_onde = None      # tableau numpy des crêtes (0..1) ou None
        self.token_onde = 0         # jeton de la piste en cours de décodage
        self.ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

        # --- Repères / commentaires (persistés sur disque) ---
        # {chemin_fichier: [{"temps": ms, "texte": "..."}, ...]}
        self.commentaires = self._charger_commentaires()
        self.duree_actuelle = 0       # durée (ms) de la piste courante (via ffmpeg)
        self.position_precedente = 0  # pour détecter le passage d'un repère
        self._apres_alerte = None     # minuterie qui efface l'alerte affichée
        self._apres_pulse = None      # minuterie du clignotement plein écran
        self._pulse_on = False        # clignotement actif ?
        self._pulse_etat = False      # bascule de couleur du clignotement
        # Palette parcourue à chaque alerte (une couleur différente à chaque fois)
        self._couleurs_alerte = ["#e53935", "#1e88e5", "#43a047", "#8e24aa",
                                 "#fb8c00", "#00897b", "#d81b60", "#3949ab"]
        self._index_couleur = 0
        self.position_demandee = None # position (0..1) à appliquer au prochain play

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

        # --- Barre du haut : titre + alerte de repère (en grand, en rouge) ---
        cadre_haut = tk.Frame(self.racine, bg=FOND)
        cadre_haut.pack(fill="x", pady=(10, 4))

        self.label_titre = tk.Label(cadre_haut, text="Aucune piste",
                                    font=("Segoe UI", 24, "bold"),
                                    bg=FOND, fg=TEXTE)
        self.label_titre.pack(side="left", padx=10)

        # --- Playlist ---
        cadre_liste = tk.Frame(self.racine, bg=FOND)
        cadre_liste.pack(fill="both", expand=True, padx=10, pady=(0, 5))

        self.liste = tk.Listbox(cadre_liste, activestyle="none",
                                bg=FOND_CLAIR, fg=TEXTE,
                                selectbackground=VERT, selectforeground=FOND,
                                highlightthickness=0, borderwidth=0)
        self.liste.pack(side="left", fill="both", expand=True)
        # Simple clic = afficher la courbe (sans lire) ; double-clic = lire
        self.liste.bind("<<ListboxSelect>>", self._selection_changee)
        self.liste.bind("<Double-Button-1>", self._lire_selection)

        defilement = tk.Scrollbar(cadre_liste, command=self.liste.yview)
        defilement.pack(side="right", fill="y")
        self.liste.config(yscrollcommand=defilement.set)

        # --- Infos du fichier (codec, fréquence, canaux, débit, durée) ---
        self.label_infos = tk.Label(self.racine, text="",
                                    font=("Segoe UI", 9), bg=FOND, fg="#9e9e9e")
        self.label_infos.pack(pady=(0, 4))

        # --- Forme d'onde : clic gauche = déplacer, clic droit = repère ---
        self.canvas_onde = tk.Canvas(self.racine, height=170, bg=FOND_CLAIR,
                                     highlightthickness=0)
        self.canvas_onde.pack(fill="x", padx=10, pady=(0, 6))
        self.canvas_onde.bind("<Button-1>", self._clic_onde)
        self.canvas_onde.bind("<Button-3>", self._clic_droit_onde)
        self.canvas_onde.bind("<Motion>", self._survol_onde)
        self.canvas_onde.bind("<Leave>",
                              lambda e: self.canvas_onde.delete("infobulle"))
        self.canvas_onde.bind("<Configure>", lambda e: self._dessiner_onde())

        # --- Boutons d'accès rapide aux repères (un par commentaire) ---
        self.cadre_reperes = tk.Frame(self.racine, bg=FOND)
        self.cadre_reperes.pack(fill="x", padx=10, pady=(0, 4))

        # --- Compteurs empilés : écoulé (vert) au-dessus, restant (rouge) en dessous ---
        cadre_compteurs = tk.Frame(self.racine, bg=FOND)
        cadre_compteurs.pack(fill="x", padx=10)

        # Police à chasse fixe (Consolas) : tous les chiffres ont la même
        # largeur, donc les compteurs ne bougent plus quand les ms défilent.
        self.label_ecoule = tk.Label(cadre_compteurs, text="00:00.000",
                                     font=("Consolas", 54), width=10,
                                     anchor="center", bg=FOND, fg=VERT)
        self.label_ecoule.pack()

        self.label_restant = tk.Label(cadre_compteurs, text="-00:00.000",
                                      font=("Consolas", 54), width=10,
                                      anchor="center", bg=FOND, fg=ROUGE)
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

        # Temps total de la playlist, à côté des boutons
        self.label_total = tk.Label(cadre_boutons, text="Σ 00:00",
                                    font=("Segoe UI", 14), bg=FOND, fg=TEXTE)
        self.label_total.pack(side="left", padx=(14, 3))

        # --- Aller à un temps précis ---
        cadre_aller = tk.Frame(self.racine, bg=FOND)
        cadre_aller.pack(pady=(0, 4))
        tk.Label(cadre_aller, text="Aller à :", bg=FOND, fg=TEXTE,
                 font=("Segoe UI", 12)).pack(side="left", padx=(0, 6))
        self.champ_aller = tk.Entry(cadre_aller, width=11, justify="center",
                                    bg=FOND_CLAIR, fg=TEXTE, insertbackground=TEXTE,
                                    relief="flat", font=("Consolas", 14))
        self.champ_aller.pack(side="left")
        self.champ_aller.bind("<Return>", self._aller_a)
        tk.Button(cadre_aller, text="→", command=self._aller_a,
                  **style_bouton).pack(side="left", padx=6)

        # --- Volume ---
        cadre_volume = tk.Frame(self.racine, bg=FOND)
        cadre_volume.pack(fill="x", padx=10, pady=(0, 6))

        tk.Label(cadre_volume, text="🔊", bg=FOND, fg=TEXTE).pack(side="left")
        self.volume = ttk.Scale(cadre_volume, from_=0, to=100,
                                orient="horizontal", command=self._sur_volume)
        self.volume.set(100)
        self.volume.pack(side="left", fill="x", expand=True, padx=6)
        self.player.audio_set_volume(100)

        # --- Ajout de fichiers ---
        tk.Button(self.racine, text="➕ Ajouter des fichiers…",
                  command=self.ajouter_fichiers,
                  **style_bouton).pack(pady=(0, 10))

        # --- Overlay d'alerte : fenêtre rouge semi-transparente (cachée) ---
        # Une Toplevel permet la transparence (attribut -alpha), impossible sur
        # un simple cadre. On la pose par-dessus la fenêtre pendant le décompte.
        self.overlay = tk.Toplevel(self.racine)
        self.overlay.withdraw()
        self.overlay.overrideredirect(True)        # sans bordure ni barre de titre
        self.overlay.attributes("-topmost", True)
        self.overlay.configure(bg="#ff0000")
        self.overlay.bind("<space>", self._touche_espace)  # garde le play/pause
        self.label_overlay_texte = tk.Label(
            self.overlay, text="", fg="#ffffff", bg="#ff0000",
            font=("Segoe UI", 80, "bold"), wraplength=1500, justify="center")
        self.label_overlay_texte.pack(pady=(60, 10))
        self.label_overlay_chiffre = tk.Label(
            self.overlay, text="", fg="#ffffff", bg="#ff0000",
            font=("Segoe UI", 150, "bold"))
        self.label_overlay_chiffre.pack(expand=True)

    # ------------------------------------------------------------------ #
    #  Gestion de la playlist
    # ------------------------------------------------------------------ #
    def ajouter_fichiers(self):
        fichiers = filedialog.askopenfilenames(
            title="Choisir des fichiers audio",
            filetypes=[("Audio", "*.mp3 *.wav *.flac *.ogg *.m4a *.aac"),
                       ("Tous les fichiers", "*.*")])
        nouveaux = []
        for chemin in fichiers:
            self.playlist.append(chemin)
            self.durees.append(0)   # durée remplie par le thread d'infos
            index = len(self.playlist) - 1
            # Ligne provisoire : le nom, en attendant les infos (…)
            self.liste.insert("end", os.path.basename(chemin) + "   —   …")
            nouveaux.append((index, chemin))
        # Calcul des infos en arrière-plan (un seul thread, séquentiel)
        if nouveaux:
            threading.Thread(target=self._charger_infos_liste,
                             args=(nouveaux,), daemon=True).start()

    def _charger_infos_liste(self, elements):
        """Lit les infos de chaque fichier ajouté et met à jour sa ligne."""
        for index, chemin in elements:
            infos, duree = self._infos_fichier(chemin)
            self.racine.after(0, lambda i=index, c=chemin, inf=infos, d=duree:
                              self._maj_ligne_liste(i, c, inf, d))

    def _maj_ligne_liste(self, index, chemin, infos, duree):
        """Remplace la ligne `index` par « nom — infos » (sélection préservée)."""
        if not (0 <= index < len(self.playlist)) or self.playlist[index] != chemin:
            return  # la playlist a changé entre-temps
        if index < len(self.durees):
            self.durees[index] = duree
            self._maj_temps_total()
        texte = os.path.basename(chemin)
        if infos:
            texte += "   —   " + infos
        selection = self.liste.curselection()
        self.liste.delete(index)
        self.liste.insert(index, texte)
        if index in selection or index == self.index_courant:
            self.liste.selection_set(index)

    def _maj_temps_total(self):
        """Met à jour l'affichage du temps cumulé de la playlist."""
        self.label_total.config(text="Σ " + self._formate_total(sum(self.durees)))

    def _formate_total(self, millisecondes):
        """Durée cumulée en H:MM:SS (ou MM:SS si moins d'une heure)."""
        secondes = int(millisecondes // 1000)
        heures, secondes = divmod(secondes, 3600)
        minutes, secondes = divmod(secondes, 60)
        if heures:
            return f"{heures}:{minutes:02d}:{secondes:02d}"
        return f"{minutes:02d}:{secondes:02d}"

    def _selection_changee(self, evenement=None):
        """Simple clic sur un fichier : on charge sa courbe sans la lire.

        Le garde-fou `!= index_courant` évite la boucle infinie : `_lire_index`
        re-sélectionne la ligne, ce qui redéclencherait cet évènement.
        """
        selection = self.liste.curselection()
        if selection and selection[0] != self.index_courant:
            self._lire_index(selection[0], lire=False)

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
            self.duree_actuelle = 0         # durée recalculée par le thread infos
            self.position_precedente = 0    # remet à zéro la détection des repères
            self.position_demandee = None   # oublie une position cliquée précédente
            self._annuler_alerte()          # stoppe un décompte/alerte en cours
            self.label_titre.config(text=os.path.basename(self.playlist[index]))
            # Surligne la piste en cours dans la liste
            self.liste.selection_clear(0, "end")
            self.liste.selection_set(index)
            # Lance le calcul de la forme d'onde + lecture des infos (sans bloquer)
            self._lancer_forme_onde(self.playlist[index])
            self._lancer_infos(self.playlist[index])
            self._maj_boutons_reperes()   # boutons d'accès rapide de cette piste
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
    def _touche_espace(self, evenement):
        """Barre espace : lecture/pause, sauf quand on tape dans « Aller à : »."""
        if self.racine.focus_get() is self.champ_aller:
            return            # laisse l'espace s'insérer dans le champ
        self.play_pause()
        return "break"

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
        self._dessiner_reperes()

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
        """Place le curseur à l'endroit cliqué (désactivé pendant la lecture).

        Fonctionne même si le fichier n'a jamais été lu : on se base sur la
        durée fournie par ffmpeg et on mémorise la position pour l'appliquer
        au prochain démarrage de la lecture.
        """
        largeur = self.canvas_onde.winfo_width()
        duree = self._duree_courante()
        if largeur <= 1 or duree <= 0:
            return
        fraction = max(0.0, min(1.0, evenement.x / largeur))
        self._appliquer_position(fraction * duree, duree)

    def _appliquer_position(self, position, duree):
        """Place lecture + curseur à `position` ms (mémorise si jamais lu).

        Sans effet pendant la lecture : on ne se déplace qu'à l'arrêt/pause
        (clic sur la courbe, champ « Aller à : », boutons de repères).
        """
        if self.player.is_playing():
            return
        position = max(0, min(position, duree))
        fraction = position / duree
        if self.player.get_length() > 0:
            # Média démarré (lecture ou pause) : saut absolu immédiat.
            self.player.set_time(int(position))
            self.position_demandee = None
            # On cale l'interpolation sur la nouvelle position pour éviter que
            # les compteurs reviennent en arrière le temps que VLC rattrape.
            self.dernier_temps_vlc = int(position)
            self.temps_reference = position
            self.horloge_reference = time.perf_counter()
        else:
            # Jamais lu : on retient la position pour le prochain play.
            self.position_demandee = fraction
            self.horloge_reference = None
        self.barre.set(fraction * 1000)
        self.position_precedente = position   # base propre pour les repères
        self.label_ecoule.config(text=formate_temps(position))
        self.label_restant.config(
            text=formate_temps(duree - position, negatif=True))
        self._maj_curseur_onde(position, duree)

    def _aller_a(self, evenement=None):
        """Saute au temps saisi dans le champ « Aller à : »."""
        # On rend le focus à la fenêtre : sinon la barre espace s'insère dans
        # le champ au lieu de relancer la lecture.
        self.racine.focus_set()
        if self.index_courant < 0:
            return
        position = self._parser_temps(self.champ_aller.get())
        duree = self._duree_courante()
        if position is None or duree <= 0:
            return
        self._appliquer_position(position, duree)
        # Propose de poser un cue (repère) à ce temps
        temps = int(max(0, min(position, duree)))
        if messagebox.askyesno(
                "Cue", f"Créer un cue à {formate_temps(temps)} ?",
                parent=self.racine):
            self._ajouter_repere(temps)

    def _parser_temps(self, texte):
        """Convertit « mm:ss(.mmm) », « h:mm:ss » ou « ss » en ms (None si KO)."""
        texte = texte.strip().replace(",", ".")
        if not texte:
            return None
        try:
            morceaux = [float(p) for p in texte.split(":")]
        except ValueError:
            return None
        if any(p < 0 for p in morceaux):
            return None
        if len(morceaux) == 1:
            secondes = morceaux[0]
        elif len(morceaux) == 2:
            secondes = morceaux[0] * 60 + morceaux[1]
        elif len(morceaux) == 3:
            secondes = morceaux[0] * 3600 + morceaux[1] * 60 + morceaux[2]
        else:
            return None
        return int(secondes * 1000)

    # ------------------------------------------------------------------ #
    #  Repères / commentaires sur la forme d'onde
    # ------------------------------------------------------------------ #
    def _duree_courante(self):
        """Durée de la piste (ms) : VLC si dispo, sinon valeur ffmpeg."""
        duree = self.player.get_length()
        return duree if duree > 0 else self.duree_actuelle

    def _reperes_courants(self):
        """Liste des repères de la piste en cours (créée si absente)."""
        if self.index_courant < 0:
            return []
        return self.commentaires.setdefault(self.playlist[self.index_courant], [])

    def _dessiner_reperes(self):
        """Dessine un drapeau orange par repère, à sa position temporelle."""
        c = self.canvas_onde
        c.delete("repere")
        duree = self._duree_courante()
        if duree <= 0 or self.index_courant < 0:
            return
        largeur, hauteur = c.winfo_width(), c.winfo_height()
        for cm in self.commentaires.get(self.playlist[self.index_courant], []):
            x = (cm["temps"] / duree) * largeur
            c.create_line(x, 0, x, hauteur, fill="#ffb300", width=2, tags="repere")
            c.create_polygon(x, 0, x + 12, 5, x, 11,
                             fill="#ffb300", outline="", tags="repere")

    def _repere_proche(self, x_clic):
        """Renvoie le repère dont le drapeau est à <8 px du clic, sinon None."""
        duree = self._duree_courante()
        if duree <= 0:
            return None
        largeur = self.canvas_onde.winfo_width()
        for cm in self.commentaires.get(self.playlist[self.index_courant], []):
            if abs(x_clic - (cm["temps"] / duree) * largeur) <= 8:
                return cm
        return None

    def _clic_droit_onde(self, evenement):
        """Clic droit : supprime le repère cliqué, sinon en crée un nouveau."""
        if self.index_courant < 0:
            return
        duree = self._duree_courante()
        largeur = self.canvas_onde.winfo_width()
        if duree <= 0 or largeur <= 1:
            return
        # Sur un repère existant -> proposer la suppression
        existant = self._repere_proche(evenement.x)
        if existant:
            if messagebox.askyesno(
                    "Supprimer le repère",
                    f"Supprimer ce repère ?\n\n"
                    f"{formate_temps(existant['temps'])}  —  {existant['texte']}",
                    parent=self.racine):
                self._reperes_courants().remove(existant)
                self._sauver_commentaires()
                self._dessiner_onde()
                self._maj_boutons_reperes()
            return
        # Sinon -> nouveau repère au temps cliqué
        temps = int(max(0.0, min(1.0, evenement.x / largeur)) * duree)
        self._ajouter_repere(temps)

    def _ajouter_repere(self, temps):
        """Demande un texte et crée un repère (cue) au temps donné (ms)."""
        texte = simpledialog.askstring(
            "Nouveau cue",
            f"Commentaire à {formate_temps(temps)} :", parent=self.racine)
        if texte:
            reperes = self._reperes_courants()
            reperes.append({"temps": temps, "texte": texte})
            reperes.sort(key=lambda d: d["temps"])
            self._sauver_commentaires()
            self._dessiner_onde()
            self._maj_boutons_reperes()

    def _maj_boutons_reperes(self):
        """Reconstruit la rangée de boutons (un par repère de la piste)."""
        for widget in self.cadre_reperes.winfo_children():
            widget.destroy()
        if self.index_courant < 0:
            return
        for cm in self.commentaires.get(self.playlist[self.index_courant], []):
            texte = cm["texte"]
            if len(texte) > 25:
                texte = texte[:24] + "…"
            libelle = f"{self._formate_total(cm['temps'])} ⟶ {texte}"
            tk.Button(self.cadre_reperes, text=libelle,
                      command=lambda t=cm["temps"]: self._aller_repere(t),
                      bg=FOND_CLAIR, fg="#ffb300", activebackground="#333333",
                      activeforeground="#ffb300", relief="flat", borderwidth=0,
                      highlightthickness=0, font=("Segoe UI", 11),
                      padx=8, pady=4).pack(side="left", padx=3)

    def _aller_repere(self, temps):
        """Va à l'instant du repère (sans effet pendant la lecture)."""
        duree = self._duree_courante()
        if duree > 0:
            self._appliquer_position(temps, duree)

    def _survol_onde(self, evenement):
        """Affiche le texte du repère survolé dans une petite bulle."""
        c = self.canvas_onde
        c.delete("infobulle")
        cm = self._repere_proche(evenement.x)
        if not cm:
            return
        largeur = c.winfo_width()
        px = min(max(evenement.x, 70), largeur - 70)
        t = c.create_text(px, 22, text=cm["texte"], fill="#000000",
                          font=("Segoe UI", 9, "bold"), tags="infobulle")
        x1, y1, x2, y2 = c.bbox(t)
        c.create_rectangle(x1 - 5, y1 - 3, x2 + 5, y2 + 3,
                           fill="#ffb300", outline="", tags="infobulle")
        c.tag_raise(t)

    def _charger_commentaires(self):
        try:
            with open(self._chemin_stockage(), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _sauver_commentaires(self):
        try:
            with open(self._chemin_stockage(), "w", encoding="utf-8") as f:
                json.dump(self.commentaires, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _chemin_stockage(self):
        """Fichier JSON des repères, à côté du script (ou de l'exe)."""
        if getattr(sys, "frozen", False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, "commentaires.json")

    def _verifier_reperes(self, position):
        """Lance un décompte de 3 s avant chaque repère franchi."""
        if self.index_courant >= 0:
            for cm in self.commentaires.get(self.playlist[self.index_courant], []):
                seuil = cm["temps"] - 3000   # 3 s de décompte avant l'événement
                if self.position_precedente < seuil <= position:
                    self._declencher_decompte(cm)
                    break
        self.position_precedente = position

    def _annuler_alerte(self):
        """Stoppe un décompte/clignotement en cours et masque l'overlay."""
        self._pulse_on = False
        for handle in (self._apres_alerte, self._apres_pulse):
            if handle is not None:
                self.racine.after_cancel(handle)
        self._apres_alerte = None
        self._apres_pulse = None
        self.overlay.withdraw()
        self.label_overlay_texte.config(text="")
        self.label_overlay_chiffre.config(text="")

    def _declencher_decompte(self, commentaire):
        """Flash coloré semi-transparent + 3·2·1, puis message à l'instant T."""
        self._annuler_alerte()
        # Une couleur différente à chaque alerte (parcours de la palette)
        couleur = self._couleurs_alerte[
            self._index_couleur % len(self._couleurs_alerte)]
        self._index_couleur += 1
        for widget in (self.overlay, self.label_overlay_texte,
                       self.label_overlay_chiffre):
            widget.config(bg=couleur)
        # Cale la fenêtre d'overlay exactement sur la fenêtre principale
        self.racine.update_idletasks()
        x, y = self.racine.winfo_rootx(), self.racine.winfo_rooty()
        largeur, hauteur = self.racine.winfo_width(), self.racine.winfo_height()
        self.overlay.geometry(f"{largeur}x{hauteur}+{x}+{y}")
        self.overlay.deiconify()
        self.overlay.lift()
        self._pulse_on = True
        self._pulser()
        self._etape_decompte(commentaire, 3)

    def _pulser(self):
        """Fait clignoter la transparence : on voit le lecteur au travers."""
        if not self._pulse_on:
            return
        self._pulse_etat = not self._pulse_etat
        self.overlay.attributes("-alpha", 0.55 if self._pulse_etat else 0.12)
        self._apres_pulse = self.racine.after(280, self._pulser)

    def _etape_decompte(self, commentaire, n):
        if n > 0:
            # Décompte : commentaire annoncé + gros chiffre
            self.label_overlay_texte.config(text=f"« {commentaire['texte']} »")
            self.label_overlay_chiffre.config(text=str(n))
            self._apres_alerte = self.racine.after(
                1000, lambda: self._etape_decompte(commentaire, n - 1))
        else:
            # Instant du repère : on coupe l'alerte aussitôt.
            self._annuler_alerte()

    # ------------------------------------------------------------------ #
    #  Informations du fichier
    # ------------------------------------------------------------------ #
    def _lancer_infos(self, chemin):
        """Lit les métadonnées du fichier en arrière-plan (via ffmpeg)."""
        self.label_infos.config(text="…")
        token = self.token_onde   # même jeton que la forme d'onde
        threading.Thread(target=self._charger_infos,
                         args=(chemin, token), daemon=True).start()

    def _infos_fichier(self, chemin):
        """Renvoie la chaîne d'infos (codec, Hz, canaux, débit, durée) ou ''.

        `ffmpeg -i fichier` sans sortie décrit les flux sur stderr puis quitte
        en erreur (normal, on ignore le code de retour).
        """
        try:
            res = subprocess.run(
                [self.ffmpeg, "-hide_banner", "-i", chemin],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            texte = res.stderr.decode("utf-8", "ignore")
        except Exception:
            return "", 0
        return self._extraire_infos(texte), self._extraire_duree(texte)

    def _extraire_duree(self, texte):
        """Durée en millisecondes depuis la ligne `Duration:` de ffmpeg."""
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", texte)
        if not m:
            return 0
        h, mn, s, cs = (int(m.group(i)) for i in range(1, 5))
        return ((h * 3600 + mn * 60 + s) * 1000) + cs * 10  # cs = centièmes

    def _charger_infos(self, chemin, token):
        infos, duree = self._infos_fichier(chemin)
        if token == self.token_onde:
            self.racine.after(0, lambda: self._afficher_infos(infos, duree, token))

    def _extraire_infos(self, texte):
        """Extrait codec / fréquence / canaux / débit / durée du texte ffmpeg."""
        parties = []
        flux = re.search(
            r"Audio:\s*([^,(]+).*?(\d+)\s*Hz,\s*([^,]+)", texte)
        if flux:
            parties.append(flux.group(1).strip().split()[0].upper())  # codec
            parties.append(f"{int(flux.group(2))} Hz")                # fréquence
            canaux = flux.group(3).strip()
            parties.append({"mono": "mono", "stereo": "stéréo"}.get(canaux, canaux))
        debit = re.search(r"Audio:.*?(\d+)\s*kb/s", texte)
        if not debit:
            debit = re.search(r"bitrate:\s*(\d+)\s*kb/s", texte)
        if debit:
            parties.append(f"{debit.group(1)} kb/s")
        duree = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", texte)
        if duree:
            h, mn, s = int(duree.group(1)), int(duree.group(2)), int(duree.group(3))
            if h:
                parties.append(f"{h}:{mn:02d}:{s:02d}")
            else:
                parties.append(f"{mn:02d}:{s:02d}")
        return "   ·   ".join(parties)

    def _afficher_infos(self, infos, duree, token):
        if token == self.token_onde:
            self.label_infos.config(text=infos)
            self.duree_actuelle = duree
            # La durée est maintenant connue : on (re)dessine les repères.
            self._dessiner_onde()

    # ------------------------------------------------------------------ #
    #  Rafraîchissement automatique
    # ------------------------------------------------------------------ #
    def _rafraichir(self):
        if not self.utilisateur_glisse and self.player.is_playing():
            duree = self.player.get_length()
            # Position demandée avant lecture (clic sur un fichier jamais lu) :
            # on l'applique dès que VLC connaît la durée, puis on saute une frame.
            if self.position_demandee is not None and duree > 0:
                self.player.set_position(self.position_demandee)
                self.position_demandee = None
                self.horloge_reference = None
                self.racine.after(16, self._rafraichir)
                return
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
                self._verifier_reperes(position)
        elif not self.player.is_playing():
            # En pause/arrêt : on coupe l'interpolation pour repartir propre.
            self.horloge_reference = None
            # On garde la référence à jour pour ne pas alerter en reprenant.
            temps = self.player.get_time()
            if temps >= 0:
                self.position_precedente = temps

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
