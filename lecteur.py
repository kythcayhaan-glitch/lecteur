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
import ctypes
from ctypes import wintypes
import tkinter as tk
from tkinter import ttk, filedialog, font as tkfont

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
import socket
import queue
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
try:
    import qrcode                      # QR code de l'URL de la télécommande
except ImportError:
    qrcode = None
import numpy as np
import imageio_ffmpeg


# --- Thèmes : fond (gris façon tableau de bord / noir d'origine) et couleur
# d'accent, choisis par l'utilisateur (menu Thème) et persistés dans
# theme.json à côté du script/exe. Pris en compte au prochain lancement
# (beaucoup de couleurs ci-dessous servent de valeurs par défaut de paramètres,
# figées à la définition des méthodes : un changement à chaud ne les
# toucherait pas toutes, d'où le rechargement seulement au démarrage).
THEMES_FOND = {
    "gris": {"FOND": "#262626", "FOND_CLAIR": "#3a3a3a", "FOND_CART": "#4d4d4d",
             "SURVOL_CLAIR": "#4a4a4a", "APPUI_CLAIR": "#2a2a2a"},
    "noir": {"FOND": "#000000", "FOND_CLAIR": "#1a1a1a", "FOND_CART": "#333333",
             "SURVOL_CLAIR": "#2d2d2d", "APPUI_CLAIR": "#101010"},
}
THEMES_ACCENT = {
    "vert": "#00e676",
    "bleu": "#2979ff",
    "orange": "#ff9100",
    "violet": "#b388ff",
}


def _chemin_theme():
    """Fichier JSON du thème choisi, à côté du script (ou de l'exe)."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "theme.json")


def _charger_theme():
    try:
        with open(_chemin_theme(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    fond = data.get("fond") if data.get("fond") in THEMES_FOND else "gris"
    accent = data.get("accent") if data.get("accent") in THEMES_ACCENT else "vert"
    return fond, accent


def _sauver_theme(fond, accent):
    try:
        with open(_chemin_theme(), "w", encoding="utf-8") as f:
            json.dump({"fond": fond, "accent": accent}, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


THEME_FOND, THEME_ACCENT = _charger_theme()
_palette_fond = THEMES_FOND[THEME_FOND]

# --- Palette de couleurs (calculée d'après le thème choisi ci-dessus) ---
FOND = _palette_fond["FOND"]              # fond général du lecteur
FOND_CLAIR = _palette_fond["FOND_CLAIR"]  # cartes / panneaux / listes
FOND_CART = _palette_fond["FOND_CART"]    # boutons des carts (contraste sur le
                                          # panneau du cartouchier, lui-même en FOND_CLAIR)
SURVOL_CLAIR = _palette_fond["SURVOL_CLAIR"]  # survol des éléments sur FOND_CLAIR
APPUI_CLAIR = _palette_fond["APPUI_CLAIR"]    # appui des éléments sur FOND_CLAIR
TEXTE = "#e0e0e0"       # gris clair : textes neutres
VERT = THEMES_ACCENT[THEME_ACCENT]  # couleur d'accent (compteur écoulé, etc.)
ROUGE = "#ff5252"       # compteur restant (négatif) : toujours rouge, quel que
                        # soit l'accent (sémantique de négatif/erreur)
FOND_JOUE = "#5c1a1a"   # fond de la portion déjà lue de la forme d'onde (rouge)
VERT_JOUE = "#c75450"   # traits de l'onde déjà lue (rouge atténué) : marque la
                        # progression en recolorant la courbe, sans rectangle


# --- Version du logiciel ---
# Format sémantique MAJEUR.MINEUR.CORRECTIF. À incrémenter à chaque
# « push et compile » ; pense à reporter le même numéro dans version_info.txt
# (propriétés de l'exe). La date de build affichée vient, elle, de la date du
# fichier (exe gelé ou source) : elle se met à jour toute seule.
VERSION = "1.2.0"


class _BoutonCanvasBase(tk.Canvas):
    """Base commune aux boutons dessinés à la main (pilule, rond) : tk.Button
    ne sait dessiner ni coins arrondis ni cercles sous Windows.

    Émule le sous-ensemble de l'API tk.Button utilisé ailleurs dans le fichier
    (config(text=..., state=...), lecture de ["state"], .pack/.focus_set) pour
    rester un remplacement transparent partout où un bouton classique servait.
    Les sous-classes fournissent juste la forme (_dessiner_forme).
    """

    def __init__(self, parent, largeur, hauteur, texte, commande, bg, fg,
                 survol, appui, bg_desactive, fg_desactive, font):
        self._commande = commande
        self._bg, self._fg = bg, fg
        self._survol = survol or bg
        self._appui = appui or bg
        self._bg_desactive, self._fg_desactive = bg_desactive, fg_desactive
        self._etat = "normal"
        self._largeur, self._hauteur = largeur, hauteur

        super().__init__(parent, width=largeur, height=hauteur,
                         bg=parent["bg"], highlightthickness=0, borderwidth=0,
                         cursor="hand2")

        self._forme = self._dessiner_forme(largeur, hauteur, bg)
        self._texte_id = self.create_text(largeur / 2, hauteur / 2, text=texte,
                                          fill=fg, font=font)

        self.bind("<Enter>", self._survoler)
        self.bind("<Leave>", self._quitter)
        self.bind("<ButtonPress-1>", self._presser)
        self.bind("<ButtonRelease-1>", self._relacher)

    def _dessiner_forme(self, largeur, hauteur, couleur):
        raise NotImplementedError

    def _survoler(self, evenement=None):
        if self._etat != "disabled":
            self.itemconfig(self._forme, fill=self._survol)

    def _quitter(self, evenement=None):
        if self._etat != "disabled":
            self.itemconfig(self._forme, fill=self._bg)

    def _presser(self, evenement=None):
        if self._etat != "disabled":
            self.itemconfig(self._forme, fill=self._appui)

    def _relacher(self, evenement):
        if self._etat == "disabled":
            return
        dans_zone = (0 <= evenement.x <= self._largeur
                     and 0 <= evenement.y <= self._hauteur)
        self.itemconfig(self._forme, fill=self._survol if dans_zone else self._bg)
        if dans_zone and self._commande:
            self._commande()

    def config(self, **kw):
        if "text" in kw:
            self.itemconfig(self._texte_id, text=kw.pop("text"))
        if "state" in kw:
            self._etat = kw.pop("state")
            if self._etat == "disabled":
                self.itemconfig(self._forme, fill=self._bg_desactive)
                self.itemconfig(self._texte_id, fill=self._fg_desactive)
                super().config(cursor="arrow")
            else:
                self.itemconfig(self._forme, fill=self._bg)
                self.itemconfig(self._texte_id, fill=self._fg)
                super().config(cursor="hand2")
        if kw:
            super().config(**kw)
    configure = config

    def __getitem__(self, cle):
        if cle == "state":
            return self._etat
        return super().__getitem__(cle)


class BoutonPilule(_BoutonCanvasBase):
    """Bouton en forme de pilule (coins totalement arrondis), pour les
    actions avec libellé texte (fichiers, édition, dialogues...)."""

    def __init__(self, parent, texte, commande=None, bg=FOND_CLAIR, fg=TEXTE,
                 survol=None, appui=None, bg_desactive=APPUI_CLAIR,
                 fg_desactive="#7a7a7a", font=("Segoe UI", 12), padx=10, pady=7,
                 largeur_min=None):
        mesure = tkfont.Font(font=font)
        largeur = max(mesure.measure(texte) + 2 * padx, largeur_min or 0)
        hauteur = mesure.metrics("linespace") + 2 * pady
        super().__init__(parent, largeur, hauteur, texte, commande, bg, fg,
                         survol, appui, bg_desactive, fg_desactive, font)

    def _dessiner_forme(self, largeur, hauteur, couleur):
        rayon = hauteur / 2
        x1, y1, x2, y2 = 1, 1, largeur - 1, hauteur - 1
        points = [x1 + rayon, y1, x2 - rayon, y1, x2, y1, x2, y1 + rayon,
                 x2, y2 - rayon, x2, y2, x2 - rayon, y2, x1 + rayon, y2,
                 x1, y2, x1, y2 - rayon, x1, y1 + rayon, x1, y1]
        return self.create_polygon(points, smooth=True, fill=couleur, outline="")


class BoutonRond(_BoutonCanvasBase):
    """Bouton rond (cercle parfait), pour les commandes à icône seule
    (transport de lecture, façon tableau de bord domotique)."""

    def __init__(self, parent, texte, commande=None, bg=FOND_CLAIR, fg=TEXTE,
                 survol=None, appui=None, bg_desactive=APPUI_CLAIR,
                 fg_desactive="#7a7a7a", font=("Segoe UI", 16), diametre=None):
        mesure = tkfont.Font(font=font)
        cote_texte = max(mesure.measure(texte), mesure.metrics("linespace"))
        diametre = diametre or int(cote_texte * 1.9)
        super().__init__(parent, diametre, diametre, texte, commande, bg, fg,
                         survol, appui, bg_desactive, fg_desactive, font)

    def _dessiner_forme(self, largeur, hauteur, couleur):
        return self.create_oval(1, 1, largeur - 1, hauteur - 1,
                                fill=couleur, outline="")


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


# --- Télécommande Android : page web servie sur le réseau local ------------- #
EXT_AUDIO = (".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac")

PAGE_REMOTE = """<!doctype html><html lang=fr><head>
<meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Kyth Player</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#000;color:#e0e0e0;text-align:center;
 font-family:'Segoe UI',system-ui,sans-serif;-webkit-user-select:none;user-select:none}
#titre{font-size:1.4rem;font-weight:700;padding:18px 12px 4px;min-height:1.7rem}
#temps{color:#9e9e9e;font-variant-numeric:tabular-nums;margin-bottom:10px}
#barre{height:7px;background:#1a1a1a;margin:0 14px 18px;border-radius:4px;overflow:hidden}
#prog{height:100%;width:0;background:#00e676}
.rangee{display:flex;justify-content:center;gap:12px;margin:14px 8px}
button{background:#1a1a1a;color:#e0e0e0;border:0;border-radius:14px;
 font-size:1.9rem;padding:18px 0;flex:1;max-width:130px}
button:active{background:#333}
#vol{width:90%;margin:14px auto;height:34px}
#liste{margin:14px 8px 18px;text-align:left}
.piste{padding:13px 14px;background:#1a1a1a;border-radius:10px;margin:7px 0;
 overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.piste.cur{background:#0f3d29;color:#00e676;font-weight:700}
.sec{margin:6px 8px 30px;text-align:left}
.bsec{width:100%;font-size:1.05rem;padding:14px;border-radius:10px;background:#13241c;
 color:#00e676;font-weight:700}
#ajout{display:none;margin-top:10px}
.lg{display:block;width:100%;padding:13px;border-radius:10px;background:#1a1a1a;
 color:#e0e0e0;margin:8px 0;text-align:center}
#chemin{color:#9e9e9e;font-size:.8rem;word-break:break-all;margin:8px 2px}
.nav{padding:12px 14px;background:#1a1a1a;border-radius:9px;margin:6px 0;
 overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nav.fichier{color:#00e676}
#info{color:#9e9e9e;min-height:1.1rem;font-size:.85rem;margin:6px}
</style></head><body>
<div id=titre>...</div>
<div id=temps>00:00 / 00:00</div>
<div id=barre><div id=prog></div></div>
<div class=rangee>
 <button onclick="a('prev')">&#9198;</button>
 <button id=pp onclick="a('play_pause')">&#9199;</button>
 <button onclick="a('next')">&#9197;</button>
 <button onclick="a('stop')">&#9209;</button>
</div>
<input id=vol type=range min=0 max=100 value=100 oninput="a('volume',this.value)">
<div id=liste></div>
<div class=sec>
 <button class=bsec onclick="bascule()">&#10133; Ajouter des titres</button>
 <div id=ajout>
  <div id=info></div>
  <label class=lg>Envoyer un fichier du t&eacute;l&eacute;phone
   <input id=fichier type=file accept="audio/*" style="display:none" onchange="envoyer()"></label>
  <button class=lg onclick="parcourir('')">&#128193; Parcourir les fichiers du PC</button>
  <div id=chemin></div>
  <div id=nav></div>
 </div>
</div>
<script>
function a(c,v){let u='/action?cmd='+c;if(v!=null)u+='&val='+encodeURIComponent(v);
 return fetch(u).then(maj)}
function f(ms){ms=Math.max(0,ms|0);let s=(ms/1000)|0,m=(s/60)|0;s%=60;
 return (m<10?'0':'')+m+':'+(s<10?'0':'')+s}
let vg=false,vol=document.getElementById('vol');
vol.addEventListener('touchstart',()=>vg=true);
vol.addEventListener('touchend',()=>vg=false);
vol.addEventListener('mousedown',()=>vg=true);
vol.addEventListener('mouseup',()=>vg=false);
function bascule(){let d=document.getElementById('ajout');
 d.style.display=d.style.display=='block'?'none':'block';}
function envoyer(){let el=document.getElementById('fichier');let fl=el.files[0];if(!fl)return;
 let fd=new FormData();fd.append('fichier',fl);info.textContent='Envoi de '+fl.name+'...';
 fetch('/televerser',{method:'POST',body:fd}).then(r=>r.json()).then(j=>{
  info.textContent=j.ok?('Ajout\\u00e9 : '+j.nom):'\\u00c9chec de l\\'envoi';el.value='';maj();
 }).catch(()=>{info.textContent='\\u00c9chec de l\\'envoi';});}
function parcourir(ch){fetch('/parcourir?chemin='+encodeURIComponent(ch||'')).then(r=>r.json())
 .then(b=>{let n=document.getElementById('nav');n.innerHTML='';
  document.getElementById('chemin').textContent=b.chemin||'(lecteurs)';
  if(b.parent!=null){let u=document.createElement('div');u.className='nav';
   u.textContent='\\u2B06 ..';u.onclick=()=>parcourir(b.parent);n.appendChild(u);}
  b.dossiers.forEach(d=>{let e=document.createElement('div');e.className='nav';
   e.textContent='\\uD83D\\uDCC1 '+d.nom;e.onclick=()=>parcourir(d.chemin);n.appendChild(e);});
  b.fichiers.forEach(x=>{let e=document.createElement('div');e.className='nav fichier';
   e.textContent='\\u2795 '+x.nom;e.onclick=()=>{a('ajouter',x.chemin);
    e.textContent='\\u2714 '+x.nom;};n.appendChild(e);});
 });}
function maj(){fetch('/etat').then(r=>r.json()).then(e=>{
 titre.textContent=e.titre||'Aucune piste';
 temps.textContent=f(e.position)+' / '+f(e.duree);
 prog.style.width=(e.duree?100*e.position/e.duree:0)+'%';
 pp.textContent=e.lecture?'\\u23F8':'\\u25B6';
 if(!vg)vol.value=e.volume;
 if(liste.dataset.n!=e.pistes.length){liste.dataset.n=e.pistes.length;liste.innerHTML='';
  e.pistes.forEach((nom,i)=>{let d=document.createElement('div');
   d.className='piste';d.textContent=nom;d.onclick=()=>a('lire',i);liste.appendChild(d)})}
 [...liste.children].forEach((d,i)=>d.classList.toggle('cur',i==e.index));
}).catch(()=>{})}
setInterval(maj,700);maj();
</script></body></html>"""


def _extraire_fichier_multipart(ctype, corps):
    """Extrait (nom_fichier, octets) d'un corps multipart/form-data. (None,None)
    si rien trouvé. Parse manuel (pas de dépendance, robuste au binaire)."""
    m = re.search(r"boundary=([^;]+)", ctype)
    if not m:
        return None, None
    sep = ("--" + m.group(1).strip().strip('"')).encode()
    for partie in corps.split(sep):
        if b"filename=" not in partie or b"Content-Disposition" not in partie:
            continue
        entete, _, donnees = partie.partition(b"\r\n\r\n")
        fm = re.search(rb'filename="([^"]*)"', entete)
        if not fm or not fm.group(1):
            continue
        if donnees.endswith(b"\r\n"):     # retire le saut avant le prochain bord
            donnees = donnees[:-2]
        return fm.group(1).decode("utf-8", "ignore"), donnees
    return None, None


def _faire_handler_remote(app):
    """Construit la classe de gestion HTTP liée à l'application `app`."""
    class HandlerRemote(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # pas de journal sur la console

        def _envoyer(self, code, corps=b"", ctype="text/html; charset=utf-8"):
            self.send_response(code)
            if corps:
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(corps)))
            self.end_headers()
            if corps:
                self.wfile.write(corps)

        def do_GET(self):
            # Réseau local uniquement : on refuse toute IP non privée.
            if not LecteurAudio._ip_est_locale(self.client_address[0]):
                self._envoyer(403)
                return
            parties = urlsplit(self.path)
            if parties.path == "/":
                self._envoyer(200, PAGE_REMOTE.encode("utf-8"))
            elif parties.path == "/etat":
                corps = json.dumps(app._etat_remote).encode("utf-8")
                self._envoyer(200, corps, "application/json")
            elif parties.path == "/action":
                q = parse_qs(parties.query)
                cmd = q.get("cmd", [""])[0]
                val = q.get("val", [None])[0]
                if cmd:
                    app._commandes_remote.put((cmd, val))
                self._envoyer(204)
            elif parties.path == "/parcourir":
                q = parse_qs(parties.query)
                chemin = q.get("chemin", [""])[0]
                corps = json.dumps(app._parcourir_dossier(chemin)).encode("utf-8")
                self._envoyer(200, corps, "application/json")
            else:
                self._envoyer(404)

        def do_POST(self):
            if not LecteurAudio._ip_est_locale(self.client_address[0]):
                self._envoyer(403)
                return
            if urlsplit(self.path).path != "/televerser":
                self._envoyer(404)
                return
            ctype = self.headers.get("Content-Type", "")
            longueur = int(self.headers.get("Content-Length", 0) or 0)
            if "multipart/form-data" not in ctype or longueur <= 0:
                self._envoyer(400)
                return
            nom, donnees = _extraire_fichier_multipart(ctype,
                                                       self.rfile.read(longueur))
            chemin = app._enregistrer_recu(nom, donnees) if nom else None
            if chemin:
                app._commandes_remote.put(("ajouter", chemin))
                rep = {"ok": True, "nom": os.path.basename(chemin)}
            else:
                rep = {"ok": False}
            self._envoyer(200, json.dumps(rep).encode("utf-8"),
                          "application/json")
    return HandlerRemote


class LecteurAudio:
    def __init__(self, racine):
        self.racine = racine
        self.racine.title(f"Kyth Player v{VERSION}")

        # --- Adaptation à la résolution de l'écran ---
        # Toutes les tailles (polices, hauteur de la forme d'onde, fenêtre) sont
        # multipliées par un facteur calculé sur la hauteur de l'écran. Référence
        # 1440 px : l'interface garde les mêmes proportions quelle que soit la
        # résolution (réduite en 1080p, agrandie en 4K).
        ecran_l = self.racine.winfo_screenwidth()
        ecran_h = self.racine.winfo_screenheight()
        self.echelle = max(0.7, min(2.5, ecran_h / 1440))

        # Fenêtre maximisée au lancement (barre de titre conservée :
        # réduire / agrandir / fermer restent accessibles).
        self.racine.minsize(self._e(420), self._e(600))
        self.racine.configure(bg=FOND)
        self.racine.state("zoomed")

        # F11 bascule le vrai plein écran ; Échap en sort vers la fenêtre 80 %.
        self.racine.bind("<Escape>", self._quitter_plein_ecran)
        self.racine.bind("<F11>", self._basculer_plein_ecran)
        # Barre espace = lecture / pause
        self.racine.bind("<space>", self._touche_espace)

        # Écran déporté (2e moniteur) : mêmes compteurs en très grand, pour la
        # régie ou la scène. None tant qu'il n'est pas ouvert.
        self.fenetre_compteurs = None

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
        # Zoom de la forme d'onde : fenêtre visible [début, fin] en fraction de
        # la piste (0..1). 0→1 = piste entière ; molette pour zoomer/dézoomer.
        self.zoom_debut = 0.0
        self.zoom_fin = 1.0
        self._derniere_position = 0  # dernière position dessinée (curseur) en ms
        # Items persistants du canvas (réutilisés d'une image à l'autre plutôt
        # que supprimés/recréés) : sinon, à chaque frame, repeindre le rectangle
        # « déjà lu » 0→x redessinait toute la forme d'onde recouverte, et le
        # coût grimpait avec la position (lag de plus en plus fort vers la fin).
        self._id_curseur = None      # trait vertical de lecture
        # Dernier pixel dessiné : tant qu'il ne change pas, inutile de repeindre
        # (le curseur n'avance que de ~10 px/s, soit 1 pixel toutes les ~6 frames
        # à 62 images/s). On évite ainsi 5 repaints inutiles sur 6.
        self._dernier_px_curseur = None
        # Progression : ids des deux traits (G/D) par colonne de l'onde, et bord
        # « déjà lu » en pixels. On recolore en rouge les colonnes franchies au
        # lieu de poser un rectangle (coût constant au lieu de croître avec la
        # position).
        self._cols_onde = []
        self._px_joue_courant = 0

        # --- Repères / commentaires (persistés sur disque) ---
        # {chemin_fichier: [{"temps": ms, "texte": "..."}, ...]}
        self.commentaires = self._charger_commentaires()
        # --- Boucles A-B enregistrées (plusieurs boucles différentes par
        # piste, persistées sur disque) ---
        # {chemin_fichier: [{"a": ms, "b": ms, "nom": "..."}, ...]}
        self.boucles = self._charger_boucles()
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
        self._couleur_alerte_courante = self._couleurs_alerte[0]
        self.position_demandee = None # position (0..1) à appliquer au prochain play

        # --- Interpolation du temps (pour des millisecondes fluides) ---
        # VLC ne met son chrono à jour que toutes les ~250 ms : on lit donc une
        # horloge précise entre deux mises à jour pour estimer le temps réel.
        self.dernier_temps_vlc = -1      # dernière valeur brute lue chez VLC
        self.temps_reference = 0         # temps VLC au moment de la synchro
        self.horloge_reference = None    # horloge précise au moment de la synchro
        self._dernier_etat = None        # état VLC précédent (détection de fin)
        self._dernier_play_pause = 0.0   # anti-rebond de la barre espace
        # Mémorise la dernière confirmation (temps, index, réponse) : un double-clic
        # génère simple + double clic ; on réutilise la réponse pour n'afficher
        # qu'une seule boîte de dialogue.
        self._derniere_confirmation = (0.0, -1, False)

        # --- Édition audio (sélection sur la courbe + historique undo/redo) ---
        self.sel_a = None        # début de sélection (ms) ou None
        self.sel_b = None        # fin de sélection (ms) ou None
        self._historique = []    # pile d'instantanés pour l'annulation (undo)
        self._refait = []        # pile pour le rétablissement (redo)
        self._edition_en_cours = False

        self._construire_interface()
        self._maj_boutons_undo()   # ↶ ↷ désactivés tant qu'aucune édition

        # --- Cartouchier (sons déclenchés au clavier, par-dessus la playlist) ---
        # Chaque cart a son propre lecteur VLC : ils se superposent à la musique
        # et entre eux, et un ré-appui sur la touche relance le son depuis le
        # début. N'importe quelle touche du clavier peut être assignée.
        self.carts = self._charger_carts()
        self._cart_players = {}      # uid du cart -> lecteur VLC dédié
        self._cart_boutons = []      # boutons de la grille (alignés sur self.carts)
        self._render_carts()
        # Toute touche déclenche le cart associé, sauf pendant une saisie de texte.
        self.racine.bind("<KeyPress>", self._touche_cart)

        # Télécommande téléphone (réseau local) : serveur web + affichage de l'URL
        self._demarrer_telecommande()
        if self._url_remote:
            self.label_remote.config(text="📱 " + self._url_remote + "   📷 QR")
        else:
            self.label_remote.config(text="📱 télécommande indisponible")

        # Mise à jour régulière de la barre + compteurs (toutes les 500 ms)
        self._rafraichir()

    def _e(self, taille):
        """Adapte une taille (police, dimension en px) à la résolution écran."""
        return max(1, int(round(taille * self.echelle)))

    def _date_build(self):
        """Date de génération du logiciel : date de l'exe gelé (donc du build) ou,
        en mode source, date de dernière modif de lecteur.py. Sert de repère pour
        détecter un exe périmé."""
        try:
            chemin = (sys.executable if getattr(sys, "frozen", False)
                      else os.path.abspath(__file__))
            return time.strftime("%d/%m/%Y %H:%M",
                                 time.localtime(os.path.getmtime(chemin)))
        except OSError:
            return ""

    def _basculer_plein_ecran(self, evenement=None):
        """F11 : bascule entre vrai plein écran et fenêtre maximisée."""
        plein = not self.racine.attributes("-fullscreen")
        self.racine.attributes("-fullscreen", plein)
        if not plein:
            self.racine.state("zoomed")   # retour à la fenêtre maximisée

    def _quitter_plein_ecran(self, evenement=None):
        """Échap : sort du vrai plein écran en revenant à la fenêtre maximisée."""
        if self.racine.attributes("-fullscreen"):
            self.racine.attributes("-fullscreen", False)
            self.racine.state("zoomed")

    # ------------------------------------------------------------------ #
    #  Écran déporté (2e moniteur) : compteurs en très grand
    # ------------------------------------------------------------------ #
    def _moniteurs(self):
        """Rectangles (x, y, largeur, hauteur) de chaque moniteur physique,
        en coordonnées bureau virtuel (Windows uniquement)."""
        if sys.platform != "win32":
            return []
        moniteurs = []
        MonitorEnumProc = ctypes.WINFUNCTYPE(
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(wintypes.RECT), ctypes.c_double)

        def _rappel(hmonitor, hdc, rect, donnees):
            r = rect.contents
            moniteurs.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
            return 1

        ctypes.windll.user32.EnumDisplayMonitors(
            None, None, MonitorEnumProc(_rappel), 0)
        return moniteurs

    def _basculer_ecran_compteurs(self):
        """Ouvre/ferme la fenêtre des compteurs en grand sur un 2e moniteur.

        Sans second moniteur détecté, ouvre quand même un aperçu (fenêtre
        normale, déplaçable) pour voir le rendu pendant les tests.
        """
        if self.fenetre_compteurs is not None:
            self._fermer_ecran_compteurs()
            return
        moniteurs = self._moniteurs()
        if len(moniteurs) < 2:
            largeur, hauteur = self._e(900), self._e(560)
            x = self.racine.winfo_x() + self._e(60)
            y = self.racine.winfo_y() + self._e(60)
            self._creer_ecran_compteurs((x, y, largeur, hauteur), apercu=True)
            return
        x0, y0 = self.racine.winfo_x(), self.racine.winfo_y()
        principal = next(
            (m for m in moniteurs if m[0] <= x0 < m[0] + m[2]
             and m[1] <= y0 < m[1] + m[3]), moniteurs[0])
        cible = next((m for m in moniteurs if m != principal), moniteurs[-1])
        self._creer_ecran_compteurs(cible)

    def _creer_ecran_compteurs(self, rect, apercu=False):
        """Construit l'écran déporté.

        L'alerte n'y prend jamais la forme d'un flash plein écran (ça
        masquerait les compteurs, inutile pour un écran de suivi) : tout le
        contenu vit dans un cadre intérieur toujours opaque (« interieur »),
        en léger retrait ; seule une fine bordure clignote dans la couleur de
        l'alerte, avec un bandeau de texte discret sous le titre.

        En mode aperçu la fenêtre est redimensionnable (l'utilisateur peut
        l'agrandir/la maximiser) : _ajuster_ecran_compteurs reprend alors tout
        le calcul de tailles pour que le contenu remplisse la fenêtre.
        """
        x, y, largeur, hauteur = rect
        fen = tk.Toplevel(self.racine)
        fen.configure(bg=FOND)
        fen.geometry(f"{largeur}x{hauteur}+{x}+{y}")
        if apercu:
            # Fenêtre normale (bordée, déplaçable) : pas de vrai 2e écran,
            # on montre juste à quoi ça ressemble.
            fen.title("Écran compteurs (aperçu)")
        else:
            fen.overrideredirect(True)
            fen.attributes("-topmost", True)
        fen.bind("<Escape>", lambda e: self._fermer_ecran_compteurs())
        fen.protocol("WM_DELETE_WINDOW", self._fermer_ecran_compteurs)

        interieur = tk.Frame(fen, bg=FOND)
        # Sans ça, un Frame se redimensionne pour épouser ses enfants : un
        # texte trop grand aurait fait gonfler « interieur », puis la fenêtre
        # entière (c'était le bug : compteurs démesurés débordant l'écran).
        interieur.pack_propagate(False)
        self._interieur_deporte = interieur

        self.label_titre_deporte = tk.Label(
            interieur, text=self.label_titre.cget("text"), bg=FOND, fg=TEXTE,
            justify="center")
        self.label_titre_deporte.pack()
        # Bandeau d'alerte : vide et invisible hors alerte, ne recouvre rien.
        self.label_alerte_deporte = tk.Label(
            interieur, text="", bg=FOND, fg=TEXTE, justify="center")
        self.label_alerte_deporte.pack()
        bloc = tk.Frame(interieur, bg=FOND)
        bloc.pack(expand=True)
        self.label_ecoule_deporte = tk.Label(
            bloc, text=self.label_ecoule.cget("text"), bg=FOND, fg=VERT)
        self.label_ecoule_deporte.pack()
        self.label_restant_deporte = tk.Label(
            bloc, text=self.label_restant.cget("text"), bg=FOND, fg=ROUGE)
        self.label_restant_deporte.pack()

        self.fenetre_compteurs = fen
        self._ajuster_ecran_compteurs()
        # Reprend tout le calcul de tailles à chaque redimensionnement de la
        # fenêtre (agrandissement manuel, maximisation...) pour que le contenu
        # remplisse toujours la fenêtre entière.
        fen.bind("<Configure>", lambda e: self._ajuster_ecran_compteurs())

    def _ajuster_ecran_compteurs(self):
        """(Re)calcule bordure, cadre intérieur et tailles de police de
        l'écran déporté d'après la taille RÉELLE actuelle de sa fenêtre."""
        fen = self.fenetre_compteurs
        if fen is None:
            return
        largeur, hauteur = fen.winfo_width(), fen.winfo_height()
        if largeur <= 1 or hauteur <= 1:
            return
        b = max(14, int(hauteur * 0.035))
        self._bordure_deportee = b
        interieur_h, interieur_l = hauteur - 2 * b, largeur - 2 * b
        self._interieur_deporte.place(x=b, y=b, width=interieur_l,
                                      height=interieur_h)

        taille_titre = -max(16, int(interieur_h * 0.08))
        taille_bandeau = -max(28, int(interieur_h * 0.075))
        marge_haute = int(interieur_h * 0.04)

        # Taille des compteurs mesurée précisément, en largeur ET en hauteur
        # (tailles en pixels négatifs = indépendantes de la résolution/DPI) :
        # ce qui reste sous le titre et le bandeau, pour deux lignes Consolas
        # empilées, sans jamais déborder du cadre même sur un petit écran.
        ref = tkfont.Font(family="Consolas", size=-100)
        ref_largeur = ref.measure("-00:00.000")
        ref_ligne = ref.metrics("linespace")
        reserve = marge_haute + int(-taille_titre * 1.3) + int(-taille_bandeau * 1.3)
        dispo_h = max(60, interieur_h - reserve)
        par_largeur = 100 * (interieur_l * 0.85) / ref_largeur
        par_hauteur = 100 * (dispo_h / 2) / ref_ligne
        taille_temps = -max(20, int(min(par_largeur, par_hauteur)))

        self.label_titre_deporte.config(
            font=("Segoe UI", taille_titre, "bold"),
            wraplength=int(interieur_l * 0.92))
        self.label_titre_deporte.pack_configure(pady=(marge_haute, 0))
        self.label_alerte_deporte.config(
            font=("Segoe UI", taille_bandeau, "bold"),
            wraplength=int(interieur_l * 0.9))
        self.label_ecoule_deporte.config(font=("Consolas", taille_temps))
        self.label_restant_deporte.config(font=("Consolas", taille_temps))

    def _fermer_ecran_compteurs(self):
        if self.fenetre_compteurs is not None:
            try:
                self.fenetre_compteurs.destroy()
            except tk.TclError:
                pass
            self.fenetre_compteurs = None

    # ------------------------------------------------------------------ #
    #  Fenêtres de dialogue personnalisées (assorties au thème sombre)
    # ------------------------------------------------------------------ #
    def _eclaircir(self, couleur, facteur=0.22):
        """Éclaircit une couleur hexadécimale (pour l'effet de survol)."""
        couleur = couleur.lstrip("#")
        r, g, b = (int(couleur[i:i + 2], 16) for i in (0, 2, 4))
        r = int(r + (255 - r) * facteur)
        g = int(g + (255 - g) * facteur)
        b = int(b + (255 - b) * facteur)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _assombrir(self, couleur, facteur=0.25):
        """Assombrit une couleur hexadécimale (pour l'effet d'appui)."""
        couleur = couleur.lstrip("#")
        r, g, b = (int(couleur[i:i + 2], 16) for i in (0, 2, 4))
        r, g, b = (int(v * (1 - facteur)) for v in (r, g, b))
        return f"#{r:02x}{g:02x}{b:02x}"

    def _bouton_dialogue(self, parent, texte, commande, primaire=False,
                         accent=VERT):
        """Bouton en pilule (coins arrondis) pour les fenêtres de dialogue."""
        fond = accent if primaire else "#4a4a4a"
        avant = FOND if primaire else TEXTE
        survol = self._eclaircir(accent) if primaire else "#5a5a5a"
        appui = self._assombrir(accent) if primaire else APPUI_CLAIR
        return BoutonPilule(parent, texte, commande=commande, bg=fond, fg=avant,
                            survol=survol, appui=appui,
                            font=("Segoe UI", self._e(12), "bold"),
                            padx=self._e(24), pady=self._e(9))

    def _creer_bouton(self, parent, texte, commande=None, primaire=False,
                      accent=VERT, taille=15, width=None, padx=10, pady=7):
        """Bouton en pilule (coins totalement arrondis) de la fenêtre
        principale : accent plein pour l'action principale (primaire=True),
        gris ardoise sinon.
        """
        fond = accent if primaire else FOND_CLAIR
        avant = FOND if primaire else TEXTE
        survol = self._eclaircir(accent) if primaire else SURVOL_CLAIR
        appui = self._assombrir(accent) if primaire else APPUI_CLAIR
        font = ("Segoe UI", self._e(taille), "bold" if primaire else "normal")
        largeur_min = None
        if width is not None:
            largeur_min = width * tkfont.Font(font=font).measure("0")
        return BoutonPilule(parent, texte, commande=commande, bg=fond, fg=avant,
                            survol=survol, appui=appui, font=font,
                            padx=self._e(padx), pady=self._e(pady),
                            largeur_min=largeur_min)

    def _creer_carte(self, parent, titre):
        """Panneau « carte » (fond FOND_CLAIR + bandeau de titre en gras),
        façon tableau de bord : regroupe visuellement une barre d'actions.
        Renvoie (carte, entete, contenu) : empaqueter « carte », placer
        d'éventuels compléments dans « entete » (à droite du titre), remplir
        « contenu ».
        """
        carte = tk.Frame(parent, bg=FOND_CLAIR)
        entete = tk.Frame(carte, bg=FOND_CLAIR)
        entete.pack(fill="x", padx=self._e(10), pady=(self._e(6), self._e(2)))
        tk.Label(entete, text=titre, bg=FOND_CLAIR, fg=TEXTE,
                 font=("Segoe UI", self._e(11), "bold")).pack(side="left")
        contenu = tk.Frame(carte, bg=FOND_CLAIR)
        contenu.pack(fill="both", expand=True, padx=self._e(8),
                     pady=(0, self._e(8)))
        return carte, entete, contenu

    def _creer_bouton_rond(self, parent, texte, commande=None, primaire=False,
                           accent=VERT, taille=18, diametre=None):
        """Bouton rond (icône seule) : mêmes accents que _creer_bouton, pour
        les commandes de transport façon tableau de bord."""
        fond = accent if primaire else FOND_CART
        avant = FOND if primaire else TEXTE
        survol = self._eclaircir(accent) if primaire else "#606060"
        appui = self._assombrir(accent) if primaire else SURVOL_CLAIR
        font = ("Segoe UI", self._e(taille))
        return BoutonRond(parent, texte, commande=commande, bg=fond, fg=avant,
                          survol=survol, appui=appui, font=font,
                          diametre=self._e(diametre) if diametre else None)

    def _creer_dialogue(self, titre, accent):
        """Toplevel sombre, sans bordure système, centré sur la fenêtre.

        Renvoie (fenetre, cadre_contenu). La bordure colorée est obtenue avec un
        fond accentué sur lequel on pose un cadre intérieur sombre.
        """
        top = tk.Toplevel(self.racine)
        top.withdraw()
        top.overrideredirect(True)
        top.configure(bg=accent)
        top.attributes("-topmost", True)
        cadre = tk.Frame(top, bg=FOND_CLAIR)
        cadre.pack(padx=self._e(2), pady=self._e(2))   # liseré accentué de 2 px
        tk.Label(cadre, text=titre, bg=FOND_CLAIR, fg=accent,
                 font=("Segoe UI", self._e(15), "bold")).pack(
                 anchor="w", padx=self._e(24), pady=(self._e(20), self._e(6)))
        return top, cadre

    def _placer_dialogue(self, top):
        """Centre le dialogue sur la fenêtre principale et le rend modal."""
        self.racine.update_idletasks()
        top.update_idletasks()
        largeur, hauteur = top.winfo_reqwidth(), top.winfo_reqheight()
        px = self.racine.winfo_rootx() + (self.racine.winfo_width() - largeur) // 2
        py = self.racine.winfo_rooty() + (self.racine.winfo_height() - hauteur) // 3
        top.geometry(f"+{px}+{py}")
        top.deiconify()
        top.lift()
        top.grab_set()
        top.focus_force()

    def _changer_theme(self):
        """Dialogue de choix du thème (fond + accent), pris en compte au
        prochain lancement (beaucoup de couleurs sont figées à la
        construction des widgets, un changement à chaud ne serait pas fiable)."""
        top, cadre = self._creer_dialogue("Thème", VERT)
        tk.Label(cadre, text="S'applique au prochain lancement de l'application.",
                 bg=FOND_CLAIR, fg="#9e9e9e", font=("Segoe UI", self._e(11))).pack(
                 anchor="w", padx=self._e(24), pady=(0, self._e(16)))

        var_fond = tk.StringVar(value=THEME_FOND)
        var_accent = tk.StringVar(value=THEME_ACCENT)

        def ligne_fond():
            tk.Label(cadre, text="Fond", bg=FOND_CLAIR, fg=TEXTE,
                     font=("Segoe UI", self._e(12), "bold")).pack(
                     anchor="w", padx=self._e(24))
            ligne = tk.Frame(cadre, bg=FOND_CLAIR)
            ligne.pack(fill="x", padx=self._e(24), pady=(self._e(6), self._e(16)))
            for valeur, libelle in (("gris", "Gris"), ("noir", "Noir")):
                tk.Radiobutton(
                    ligne, text=libelle, variable=var_fond, value=valeur,
                    indicatoron=False, bg=FOND_CART, fg=TEXTE, selectcolor=VERT,
                    activebackground=SURVOL_CLAIR, activeforeground=TEXTE,
                    relief="flat", borderwidth=0, highlightthickness=0,
                    cursor="hand2", offrelief="flat",
                    font=("Segoe UI", self._e(12)),
                    padx=self._e(16), pady=self._e(8)).pack(side="left", padx=(0, 8))

        def ligne_accent():
            tk.Label(cadre, text="Accent", bg=FOND_CLAIR, fg=TEXTE,
                     font=("Segoe UI", self._e(12), "bold")).pack(
                     anchor="w", padx=self._e(24))
            ligne = tk.Frame(cadre, bg=FOND_CLAIR)
            ligne.pack(fill="x", padx=self._e(24), pady=(self._e(6), self._e(20)))
            for valeur, libelle in (("vert", "Vert"), ("bleu", "Bleu"),
                                    ("orange", "Orange"), ("violet", "Violet")):
                couleur = THEMES_ACCENT[valeur]
                tk.Radiobutton(
                    ligne, text=libelle, variable=var_accent, value=valeur,
                    indicatoron=False, bg=self._assombrir(couleur, 0.55), fg=FOND,
                    selectcolor=couleur, activebackground=self._eclaircir(couleur),
                    activeforeground=FOND, relief="flat", borderwidth=0,
                    highlightthickness=0, cursor="hand2", offrelief="flat",
                    font=("Segoe UI", self._e(12), "bold"),
                    padx=self._e(14), pady=self._e(8)).pack(side="left", padx=(0, 8))

        ligne_fond()
        ligne_accent()

        def valider():
            _sauver_theme(var_fond.get(), var_accent.get())
            top.destroy()
            self._info("Thème enregistré",
                       "Relance l'application pour voir le nouveau thème.")

        barre = tk.Frame(cadre, bg=FOND_CLAIR)
        barre.pack(fill="x", padx=self._e(24), pady=(0, self._e(20)))
        self._bouton_dialogue(barre, "Annuler", top.destroy).pack(
            side="right", padx=(self._e(10), 0))
        self._bouton_dialogue(barre, "Enregistrer", valider,
                              primaire=True).pack(side="right")
        top.bind("<Escape>", lambda e: top.destroy())
        self._placer_dialogue(top)

    def _demander_oui_non(self, titre, message, oui="Oui", non="Non",
                          accent=VERT):
        """Confirmation Oui/Non façon thème sombre. Renvoie True/False."""
        resultat = {"valeur": False}
        top, cadre = self._creer_dialogue(titre, accent)
        tk.Label(cadre, text=message, bg=FOND_CLAIR, fg=TEXTE, justify="left",
                 font=("Segoe UI", self._e(12)), wraplength=self._e(440)).pack(
                 anchor="w", padx=self._e(24), pady=(0, self._e(20)))
        barre = tk.Frame(cadre, bg=FOND_CLAIR)
        barre.pack(fill="x", padx=self._e(24), pady=(0, self._e(20)))

        def repondre(valeur):
            resultat["valeur"] = valeur
            top.destroy()

        self._bouton_dialogue(barre, non, lambda: repondre(False)).pack(
            side="right", padx=(self._e(10), 0))
        b_oui = self._bouton_dialogue(barre, oui, lambda: repondre(True),
                                      primaire=True, accent=accent)
        b_oui.pack(side="right")
        top.bind("<Return>", lambda e: repondre(True))
        top.bind("<Escape>", lambda e: repondre(False))
        top.bind("<space>", lambda e: "break")
        self._placer_dialogue(top)
        b_oui.focus_set()
        self.racine.wait_window(top)
        return resultat["valeur"]

    def _info(self, titre, message, accent=VERT):
        """Message d'information avec un seul bouton OK (thème sombre)."""
        top, cadre = self._creer_dialogue(titre, accent)
        tk.Label(cadre, text=message, bg=FOND_CLAIR, fg=TEXTE, justify="left",
                 font=("Segoe UI", self._e(12)), wraplength=self._e(440)).pack(
                 anchor="w", padx=self._e(24), pady=(0, self._e(20)))
        barre = tk.Frame(cadre, bg=FOND_CLAIR)
        barre.pack(fill="x", padx=self._e(24), pady=(0, self._e(20)))
        bouton = self._bouton_dialogue(barre, "OK", top.destroy, primaire=True,
                                       accent=accent)
        bouton.pack(side="right")
        top.bind("<Return>", lambda e: top.destroy())
        top.bind("<Escape>", lambda e: top.destroy())
        self._placer_dialogue(top)
        bouton.focus_set()
        self.racine.wait_window(top)

    def _demander_texte(self, titre, message, accent=VERT, defaut=""):
        """Saisie de texte façon thème sombre. Renvoie la chaîne ou None.

        `defaut` pré-remplit le champ (et est sélectionné pour remplacement direct).
        """
        resultat = {"valeur": None}
        top, cadre = self._creer_dialogue(titre, accent)
        tk.Label(cadre, text=message, bg=FOND_CLAIR, fg=TEXTE, justify="left",
                 font=("Segoe UI", self._e(12))).pack(
                 anchor="w", padx=self._e(24), pady=(0, self._e(10)))
        champ = tk.Entry(cadre, bg=FOND, fg=TEXTE, insertbackground=TEXTE,
                         relief="flat", justify="left", width=32,
                         font=("Consolas", self._e(13)))
        champ.pack(fill="x", padx=self._e(24), ipady=self._e(5),
                   pady=(0, self._e(20)))
        if defaut:
            champ.insert(0, defaut)
            champ.select_range(0, "end")
        barre = tk.Frame(cadre, bg=FOND_CLAIR)
        barre.pack(fill="x", padx=self._e(24), pady=(0, self._e(20)))

        def valider():
            resultat["valeur"] = champ.get()
            top.destroy()

        def annuler():
            resultat["valeur"] = None
            top.destroy()

        self._bouton_dialogue(barre, "Annuler", annuler).pack(
            side="right", padx=(self._e(10), 0))
        self._bouton_dialogue(barre, "Valider", valider, primaire=True,
                              accent=accent).pack(side="right")
        top.bind("<Return>", lambda e: valider())
        top.bind("<Escape>", lambda e: annuler())
        self._placer_dialogue(top)
        champ.focus_set()
        self.racine.wait_window(top)
        return resultat["valeur"]

    # ------------------------------------------------------------------ #
    #  Construction de l'interface
    # ------------------------------------------------------------------ #
    def _construire_interface(self):
        # Barre de défilement plate, assortie au thème sombre (le thème natif
        # Windows ignore les couleurs ttk : on force "clam", seul thème qui
        # les respecte).
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Sombre.Vertical.TScrollbar", background=FOND_CART,
                        troughcolor=FOND_CLAIR, bordercolor=FOND_CLAIR,
                        arrowcolor=TEXTE, relief="flat",
                        width=self._e(10), arrowsize=self._e(12))
        style.map("Sombre.Vertical.TScrollbar",
                 background=[("active", "#606060"), ("pressed", VERT)])
        # Le passage à "clam" change aussi l'apparence de la barre de
        # progression (ttk.Scale) : on l'assortit au même thème sombre.
        style.configure("Sombre.Horizontal.TScale", background=FOND,
                        troughcolor=FOND_CLAIR, darkcolor=FOND_CLAIR,
                        lightcolor=FOND_CLAIR, bordercolor=FOND_CLAIR)
        # Glissières de volume des carts : mêmes réglages, assorties au fond
        # plus clair des tuiles du cartouchier (FOND_CART).
        style.configure("Cart.Horizontal.TScale", background=FOND_CART,
                        troughcolor=FOND, darkcolor=FOND, lightcolor=FOND,
                        bordercolor=FOND_CART)

        # --- Barre du haut : titre + alerte de repère (en grand, en rouge) ---
        cadre_haut = tk.Frame(self.racine, bg=FOND)
        cadre_haut.pack(fill="x", pady=(10, 4))

        self.label_titre = tk.Label(cadre_haut, text="Aucune piste",
                                    font=("Segoe UI", self._e(24), "bold"),
                                    bg=FOND, fg=TEXTE)
        self.label_titre.pack(side="left", padx=10)

        # Version + date de build : repère permanent (visible même en plein écran)
        # pour éviter de tester un exe périmé. La date vient de la date du fichier.
        self.label_version = tk.Label(
            cadre_haut, text=f"v{VERSION} · {self._date_build()}", bg=FOND,
            fg="#6e6e6e", font=("Segoe UI", self._e(10)))
        self.label_version.pack(side="left", anchor="s", pady=(0, self._e(8)))

        # Adresse de la télécommande (téléphone sur le réseau local) : cliquer
        # affiche un QR code à scanner avec le téléphone.
        self.label_remote = tk.Label(cadre_haut, text="", bg=FOND, fg="#9e9e9e",
                                     font=("Consolas", self._e(11)),
                                     cursor="hand2")
        self.label_remote.pack(side="right", padx=10)
        self.label_remote.bind("<Button-1>", self._afficher_qr)

        # Écran déporté (2e moniteur) : compteurs + titre en très grand.
        self._creer_bouton(cadre_haut, "🖥 Écran compteurs",
                  commande=self._basculer_ecran_compteurs, taille=11,
                  padx=8, pady=4).pack(side="right", padx=10)
        self._creer_bouton(cadre_haut, "🎨 Thème",
                  commande=self._changer_theme, taille=11,
                  padx=8, pady=4).pack(side="right", padx=(0, 10))

        # --- Barre de gestion des fichiers (au-dessus de la playlist) ---
        carte_fichiers, _, cadre_fichiers = self._creer_carte(self.racine, "Fichiers")
        carte_fichiers.pack(fill="x", padx=10, pady=(0, 8))
        self._creer_bouton(cadre_fichiers, "➕ Ajouter",
                  commande=self.ajouter_fichiers, taille=13,
                  ).pack(side="left", padx=5)
        self._texte_convertir = "🎚 Convertir"
        self.bouton_convertir = self._creer_bouton(cadre_fichiers,
                  self._texte_convertir, commande=self.convertir_wav, taille=13)
        self.bouton_convertir.pack(side="left", padx=5)
        self._texte_normaliser = "🔊 Normaliser"
        self.bouton_normaliser = self._creer_bouton(cadre_fichiers,
                  self._texte_normaliser, commande=self.normaliser, taille=13)
        self.bouton_normaliser.pack(side="left", padx=5)
        self._creer_bouton(cadre_fichiers, "🗑 Vider",
                  commande=self.vider_playlist, taille=13,
                  ).pack(side="left", padx=5)

        # --- Zone centrale : playlist (gauche) + timecodes (droite) ---
        cadre_central = tk.Frame(self.racine, bg=FOND)
        cadre_central.pack(fill="both", expand=True, padx=10, pady=(0, 5))

        # Playlist (colonne de gauche, largeur fixe réduite : on laisse ainsi
        # la place aux compteurs et aux infos, agrandis, dans la colonne droite)
        carte_liste, entete_liste, cadre_liste = self._creer_carte(
            cadre_central, "Playlist")
        carte_liste.pack(side="left", fill="y")
        self.label_total = tk.Label(entete_liste, text="Σ 00:00",
                                    font=("Segoe UI", self._e(11)),
                                    bg=FOND_CLAIR, fg="#9e9e9e")
        self.label_total.pack(side="right")

        self.liste = tk.Listbox(cadre_liste, activestyle="none",
                                bg=FOND_CLAIR, fg=TEXTE,
                                selectbackground=VERT, selectforeground=FOND,
                                highlightthickness=0, borderwidth=0,
                                exportselection=False, width=70,
                                font=("Segoe UI", self._e(15)))
        self.liste.pack(side="left", fill="both", expand=True)
        # Simple clic = afficher la courbe (sans lire) ; double-clic = lire.
        # On écoute le vrai clic souris (et pas <<ListboxSelect>>, qui se
        # déclenche aussi quand on change la sélection par programme — ce qui
        # rechargeait par erreur le morceau précédent après un changement auto).
        self.liste.bind("<ButtonRelease-1>", self._clic_liste)
        self.liste.bind("<Double-Button-1>", self._lire_selection)
        # Retrait d'un morceau : clic droit sur la ligne, ou touche Suppr
        self.liste.bind("<Button-3>", self._clic_droit_liste)
        self.liste.bind("<Delete>", self._supprimer_selection)

        defilement = ttk.Scrollbar(cadre_liste, orient="vertical",
                                   style="Sombre.Vertical.TScrollbar",
                                   command=self.liste.yview)
        defilement.pack(side="right", fill="y")
        self.liste.config(yscrollcommand=defilement.set)

        # Cartouchier (colonne du milieu) : grille de sons déclenchés au clavier,
        # joués par-dessus la musique de la playlist (canal audio séparé).
        self._construire_cartouchier(cadre_central)

        # Timecodes (colonne de droite) : écoulé (vert) au-dessus, restant
        # (rouge) en dessous, empilés et centrés verticalement face à la liste.
        # Police à chasse fixe (Consolas) : les chiffres gardent la même largeur,
        # donc les compteurs ne bougent pas quand les millisecondes défilent.
        self.cadre_compteurs = tk.Frame(cadre_central, bg=FOND)
        self.cadre_compteurs.pack(side="right", fill="both", expand=True,
                                  padx=(12, 0))
        bloc_compteurs = tk.Frame(self.cadre_compteurs, bg=FOND)
        bloc_compteurs.pack(expand=True, anchor="e")

        self.label_ecoule = tk.Label(bloc_compteurs, text="00:00.000",
                                     font=("Consolas", self._e(84)), width=10,
                                     anchor="e", bg=FOND, fg=VERT)
        self.label_ecoule.pack(fill="x")
        self.label_restant = tk.Label(bloc_compteurs, text="-00:00.000",
                                      font=("Consolas", self._e(84)), width=10,
                                      anchor="e", bg=FOND, fg=ROUGE)
        self.label_restant.pack(fill="x")

        # Infos du fichier (codec, fréquence, canaux, débit, durée) : sous les
        # timers, dans la colonne de droite. La playlist ne montre que les noms.
        self.label_infos = tk.Label(bloc_compteurs, text="", justify="center",
                                    font=("Segoe UI", self._e(15)), bg=FOND,
                                    fg="#9e9e9e", wraplength=self._e(560))
        self.label_infos.pack(pady=(self._e(14), 0))

        # --- Forme d'onde : clic gauche = déplacer, clic droit = repère ---
        self.canvas_onde = tk.Canvas(self.racine, height=self._e(300),
                                     bg=FOND_CLAIR, highlightthickness=0)
        self.canvas_onde.pack(fill="x", padx=10, pady=(0, 6))
        self.canvas_onde.bind("<Button-1>", self._clic_onde)
        self.canvas_onde.bind("<Button-3>", self._clic_droit_onde)
        self.canvas_onde.bind("<MouseWheel>", self._zoom_onde)   # molette = zoom
        self.canvas_onde.bind("<Motion>", self._survol_onde)
        self.canvas_onde.bind("<Leave>",
                              lambda e: self.canvas_onde.delete("infobulle"))
        self.canvas_onde.bind("<Configure>", lambda e: self._dessiner_onde())
        # Sélection d'une zone à éditer : Shift + glisser sur la courbe.
        self.canvas_onde.bind("<Shift-Button-1>", self._debut_selection)
        self.canvas_onde.bind("<Shift-B1-Motion>", self._glisser_selection)
        self.canvas_onde.bind("<Shift-ButtonRelease-1>", self._fin_selection)

        # --- Barre d'édition audio (icônes ; agit sur la sélection) ---
        carte_edition, _, cadre_edition = self._creer_carte(
            self.racine, "Édition (Shift+glisser pour sélectionner)")
        carte_edition.pack(fill="x", padx=10, pady=(0, 8))
        self._creer_bouton(cadre_edition, "Couper",
                  commande=lambda: self._editer("couper"), taille=14,
                  padx=9, pady=5).pack(side="left", padx=4)
        self._creer_bouton(cadre_edition, "Fondu in",
                  commande=lambda: self._editer("fade_in"), taille=14,
                  padx=9, pady=5).pack(side="left", padx=4)
        self._creer_bouton(cadre_edition, "Fondu out",
                  commande=lambda: self._editer("fade_out"), taille=14,
                  padx=9, pady=5).pack(side="left", padx=4)
        self._creer_bouton(cadre_edition, "Rogner",
                  commande=lambda: self._editer("rogner"), taille=14,
                  padx=9, pady=5).pack(side="left", padx=4)
        self._creer_bouton(cadre_edition, "Sup. Sél.",
                  commande=self._effacer_selection, taille=14,
                  padx=9, pady=5).pack(side="left", padx=(14, 4))
        # Boucle A-B : reboucle la lecture sur la zone sélectionnée
        self.boucle_ab = tk.BooleanVar(value=False)
        tk.Checkbutton(cadre_edition, text="Boucle A‑B",
                  variable=self.boucle_ab, command=self._basculer_boucle_ab,
                  indicatoron=False, bg=FOND_CLAIR, fg=TEXTE, selectcolor=VERT,
                  activebackground=SURVOL_CLAIR, activeforeground=TEXTE,
                  relief="flat", borderwidth=0, highlightthickness=0,
                  cursor="hand2", offrelief="flat", font=("Segoe UI", self._e(15)),
                  padx=self._e(9), pady=self._e(5)).pack(side="left", padx=(14, 4))
        self._creer_bouton(cadre_edition, "💾 Sauver boucle",
                  commande=self._sauver_boucle_ab, taille=14,
                  padx=9, pady=5).pack(side="left", padx=(14, 4))
        self.bouton_undo = self._creer_bouton(cadre_edition, "↶",
                  commande=self.annuler_edition, taille=14, padx=9, pady=5)
        self.bouton_undo.pack(side="left", padx=(14, 4))
        self.bouton_redo = self._creer_bouton(cadre_edition, "↷",
                  commande=self.refaire_edition, taille=14, padx=9, pady=5)
        self.bouton_redo.pack(side="left", padx=4)
        self.label_selection = tk.Label(cadre_edition, text="", bg=FOND_CLAIR,
                  fg=VERT, font=("Consolas", self._e(11)))
        self.label_selection.pack(side="left", padx=10)
        self.racine.bind("<Control-z>", lambda e: self.annuler_edition())
        self.racine.bind("<Control-y>", lambda e: self.refaire_edition())

        # --- Boutons d'accès rapide aux repères (un par commentaire) ---
        self.cadre_reperes = tk.Frame(self.racine, bg=FOND)
        self.cadre_reperes.pack(fill="x", padx=10, pady=(0, 4))

        # --- Boucles A-B enregistrées (plusieurs boucles différentes par piste) ---
        self.cadre_boucles = tk.Frame(self.racine, bg=FOND)
        self.cadre_boucles.pack(fill="x", padx=10, pady=(0, 4))

        # --- Barre de progression (pleine largeur) ---
        cadre_progression = tk.Frame(self.racine, bg=FOND)
        cadre_progression.pack(fill="x", padx=10, pady=(2, 0))

        self.barre = ttk.Scale(cadre_progression, from_=0, to=1000,
                               orient="horizontal", style="Sombre.Horizontal.TScale",
                               command=self._sur_glissement)
        self.barre.pack(fill="x", expand=True)
        # On capte le clic/relâché pour ne pas se battre avec la maj automatique
        self.barre.bind("<ButtonPress-1>", self._debut_glissement)
        self.barre.bind("<ButtonRelease-1>", self._fin_glissement)

        # --- Boutons de contrôle ---
        carte_lecture, _, contenu_lecture = self._creer_carte(self.racine, "Lecture")
        carte_lecture.pack(pady=8)
        cadre_boutons = tk.Frame(contenu_lecture, bg=FOND_CLAIR)
        cadre_boutons.pack()

        self._creer_bouton_rond(cadre_boutons, "⏮",
                  commande=self.precedent, taille=18, diametre=52).pack(
                  side="left", padx=6)
        self.bouton_play = self._creer_bouton_rond(cadre_boutons, "▶",
                                     commande=self.play_pause, taille=20,
                                     primaire=True, diametre=64)
        self.bouton_play.pack(side="left", padx=6)
        self._creer_bouton_rond(cadre_boutons, "⏭",
                  commande=self.suivant, taille=18, diametre=52).pack(
                  side="left", padx=6)
        self._creer_bouton_rond(cadre_boutons, "⏹",
                  commande=self.stop, taille=18, diametre=52).pack(
                  side="left", padx=6)

        # Bouton bascule "boucle" : enfoncé = playlist en boucle continue
        self.boucle = tk.BooleanVar(value=False)
        tk.Checkbutton(cadre_boutons, text="🔁", width=4, variable=self.boucle,
                       indicatoron=False, bg=FOND_CLAIR, fg=TEXTE,
                       selectcolor=VERT, activebackground=SURVOL_CLAIR,
                       activeforeground=TEXTE, relief="flat",
                       borderwidth=0, highlightthickness=0, cursor="hand2",
                       offrelief="flat",
                       font=("Segoe UI", self._e(18)), padx=self._e(8),
                       pady=self._e(6)).pack(side="left", padx=4)

        # --- Aller à un temps précis ---
        cadre_aller = tk.Frame(contenu_lecture, bg=FOND_CLAIR)
        cadre_aller.pack(pady=(6, 0))
        tk.Label(cadre_aller, text="Aller à :", bg=FOND_CLAIR, fg=TEXTE,
                 font=("Segoe UI", self._e(12))).pack(side="left", padx=(0, 6))
        self.champ_aller = tk.Entry(cadre_aller, width=11, justify="center",
                                    bg=FOND, fg=TEXTE, insertbackground=TEXTE,
                                    relief="flat", font=("Consolas", self._e(14)))
        self.champ_aller.pack(side="left")
        self.champ_aller.bind("<Return>", self._aller_a)

        # Volume : plus de curseur visible, piloté uniquement par la télécommande
        self.volume = tk.DoubleVar(value=100)
        self.player.audio_set_volume(100)

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
            font=("Segoe UI", self._e(80), "bold"),
            wraplength=self._e(1500), justify="center")
        self.label_overlay_texte.pack(pady=(60, 10))
        self.label_overlay_chiffre = tk.Label(
            self.overlay, text="", fg="#ffffff", bg="#ff0000",
            font=("Segoe UI", self._e(150), "bold"))
        self.label_overlay_chiffre.pack(expand=True)

        # Empêche les boutons d'être « cliqués » par la barre espace quand ils
        # ont le focus : sinon Espace activerait le bouton EN PLUS du raccourci
        # play/pause global (double déclenchement -> lecture puis pause).
        self._neutraliser_espace(self.racine)

    def _neutraliser_espace(self, widget):
        """La barre espace = play/pause partout, même quand un bouton a le focus.

        On route l'espace des boutons vers _touche_espace (qui joue/met en pause
        une fois puis renvoie « break ») : ça empêche le bouton de s'activer en
        plus du raccourci, donc plus de double déclenchement.
        """
        for enfant in widget.winfo_children():
            if isinstance(enfant, (tk.Button, tk.Checkbutton)):
                enfant.bind("<space>", self._touche_espace)
                enfant.configure(takefocus=0)
            self._neutraliser_espace(enfant)

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
            # La playlist affiche le nom (+ durée, ajoutée dès qu'elle est
            # connue) ; les infos détaillées s'affichent sous les timers quand
            # la piste est sélectionnée.
            self.liste.insert("end", self._libelle_liste(chemin, 0))
            nouveaux.append((index, chemin))
        # Calcul des durées en arrière-plan (pour le temps total cumulé)
        if nouveaux:
            threading.Thread(target=self._charger_infos_liste,
                             args=(nouveaux,), daemon=True).start()

    def _clic_droit_liste(self, evenement):
        """Clic droit sur une ligne : propose de retirer ce morceau."""
        index = self.liste.nearest(evenement.y)
        if 0 <= index < len(self.playlist):
            self._retirer_morceau(index)
        return "break"

    def _supprimer_selection(self, evenement=None):
        """Touche Suppr : retire le morceau sélectionné."""
        selection = self.liste.curselection()
        if selection:
            self._retirer_morceau(selection[0])
        return "break"

    def _retirer_morceau(self, index):
        """Retire un seul morceau de la playlist (fichier/repères conservés)."""
        if not (0 <= index < len(self.playlist)):
            return
        nom = os.path.basename(self.playlist[index])
        if not self._demander_oui_non(
                "Retirer le morceau",
                f"Retirer ce morceau de la playlist ?\n\n{nom}",
                oui="Retirer", accent=ROUGE):
            return
        courant_supprime = (index == self.index_courant)
        if courant_supprime:
            self.player.stop()
            self._annuler_alerte()
        del self.playlist[index]
        if index < len(self.durees):
            del self.durees[index]
        self.liste.delete(index)
        self._maj_temps_total()
        if courant_supprime:
            # La piste en cours a disparu : on remet l'affichage à zéro.
            self.index_courant = -1
            self.token_onde += 1
            self.forme_onde = None
            self.horloge_reference = None
            self.position_demandee = None
            self.canvas_onde.delete("all")
            self._id_curseur = None       # curseur effacé : à recréer
            self._cols_onde = []          # traits de l'onde effacés
            self._px_joue_courant = 0
            self.label_titre.config(text="Aucune piste")
            self.label_infos.config(text="")
            self.label_ecoule.config(text="00:00.000")
            self.label_restant.config(text="-00:00.000")
            self.barre.set(0)
            self.bouton_play.config(text="▶")
            self._maj_boutons_reperes()
            self._maj_boutons_boucles()
        else:
            # On garde la piste courante : son index glisse si on a retiré avant.
            if index < self.index_courant:
                self.index_courant -= 1
            self._surligner_courant()

    def vider_playlist(self):
        """Retire tous les morceaux de la playlist et remet le lecteur à zéro.

        Les fichiers et leurs repères (commentaires.json) restent intacts sur le
        disque : on ne vide que la liste en cours.
        """
        if not self.playlist:
            return
        if not self._demander_oui_non(
                "Vider la playlist",
                f"Retirer les {len(self.playlist)} morceau(x) de la playlist ?\n\n"
                "Les fichiers et leurs repères ne sont pas supprimés du disque.",
                oui="Vider", accent=ROUGE):
            return
        self.player.stop()
        self._annuler_alerte()
        self.playlist.clear()
        self.durees.clear()
        self.index_courant = -1
        self.token_onde += 1            # invalide tout décodage d'onde en cours
        self.forme_onde = None
        self.horloge_reference = None
        self.position_demandee = None
        self.liste.delete(0, "end")
        self.canvas_onde.delete("all")
        self._id_curseur = None       # curseur effacé : à recréer
        self._cols_onde = []          # traits de l'onde effacés
        self._px_joue_courant = 0
        self.label_titre.config(text="Aucune piste")
        self.label_infos.config(text="")
        self.label_ecoule.config(text="00:00.000")
        self.label_restant.config(text="-00:00.000")
        self.barre.set(0)
        self.bouton_play.config(text="▶")
        self._maj_temps_total()
        self._maj_boutons_reperes()
        self._maj_boutons_boucles()

    # ------------------------------------------------------------------ #
    #  Conversion en WAV 48 kHz (timeline échantillon-exacte pour les cues)
    # ------------------------------------------------------------------ #
    def convertir_wav(self):
        """Convertit tous les fichiers de la playlist en WAV 48 kHz.

        Les MP3 (surtout VBR) donnent une position imprécise : les cues dérivent.
        Le WAV est échantillon-exact, donc les repères tombent toujours juste.
        Les originaux sont conservés ; chaque sortie est écrite en « _48k.wav »
        à côté de la source, et remplace la piste dans la playlist.
        """
        if not self.playlist:
            return
        if not self._demander_oui_non(
                "Convertir en WAV 48 kHz",
                f"Convertir les {len(self.playlist)} fichier(s) de la playlist "
                "en WAV 48 kHz ?\n\n"
                "• Les originaux sont conservés (sortie « _48k.wav »).\n"
                "• Les repères sont copiés sur les nouveaux fichiers "
                "(à revérifier).\n"
                "• La synchro des cues devient fiable (plus de dérive MP3)."):
            return
        self.bouton_convertir.config(state="disabled")
        fichiers = list(enumerate(self.playlist))
        threading.Thread(target=self._convertir_thread,
                         args=(fichiers,), daemon=True).start()

    def _convertir_thread(self, fichiers):
        total = len(fichiers)
        echecs = 0
        for rang, (index, chemin) in enumerate(fichiers, start=1):
            self.racine.after(0, lambda r=rang, t=total:
                              self.bouton_convertir.config(
                                  text=f"Conversion {r}/{t}…"))
            sortie = os.path.splitext(chemin)[0] + "_48k.wav"
            try:
                subprocess.run(
                    [self.ffmpeg, "-v", "quiet", "-y", "-i", chemin,
                     "-ar", "48000", "-c:a", "pcm_s16le", sortie],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    check=True)
            except Exception:
                echecs += 1
                continue
            _, duree = self._infos_fichier(sortie)
            self.racine.after(0, lambda i=index, c=chemin, s=sortie, d=duree:
                              self._remplacer_par_wav(i, c, s, d))
        self.racine.after(0, lambda: self._fin_conversion(echecs, total))

    def _remplacer_par_wav(self, index, ancien, nouveau, duree):
        """Remplace dans la playlist le fichier converti et migre ses repères."""
        if not (0 <= index < len(self.playlist)) or self.playlist[index] != ancien:
            return  # la playlist a changé entre-temps
        self.playlist[index] = nouveau
        if index < len(self.durees):
            self.durees[index] = duree
            self._maj_temps_total()
        # Migration des repères vers le nouveau chemin (valeurs ms conservées)
        if ancien in self.commentaires:
            self.commentaires[nouveau] = self.commentaires.pop(ancien)
            self._sauver_commentaires()
        if ancien in self.boucles:
            self.boucles[nouveau] = self.boucles.pop(ancien)
            self._sauver_boucles()
        self.liste.delete(index)
        self.liste.insert(index, self._libelle_liste(nouveau, duree))
        self._surligner_courant()
        if index == self.index_courant:
            self.label_titre.config(text=os.path.basename(nouveau))
            self._maj_boutons_reperes()
            self._maj_boutons_boucles()
            # La piste affichée vient d'être remplacée par sa version _norm.wav :
            # on redécode la forme d'onde du nouveau fichier, sinon le canvas
            # garde la courbe de l'ancien (le tracé étant calé sur le pic, le
            # changement de niveau reste invisible, mais la courbe doit refléter
            # le fichier réellement en place).
            self._lancer_forme_onde(nouveau)

    def _fin_conversion(self, echecs, total):
        self.bouton_convertir.config(state="normal", text=self._texte_convertir)
        message = f"{total - echecs}/{total} fichier(s) convertis en WAV 48 kHz."
        if echecs:
            message += f"\n\n{echecs} fichier(s) en échec."
        message += "\n\nPense à revérifier tes repères sur les nouveaux fichiers."
        self._info("Conversion terminée", message,
                   accent=ROUGE if echecs else VERT)

    # ------------------------------------------------------------------ #
    #  Normalisation du pic à -3 dBFS (niveaux homogènes entre morceaux)
    # ------------------------------------------------------------------ #
    def normaliser(self):
        """Normalise le pic de chaque morceau de la playlist à -3 dBFS.

        Mesure le pic réel (ffmpeg volumedetect) puis applique le gain qui amène
        ce pic à -3 dB (3 dB de marge sous la saturation). Les originaux sont
        conservés ; chaque sortie est écrite en « _norm.wav » à côté de la
        source, et remplace la piste dans la playlist.
        """
        if not self.playlist:
            return
        saisie = self._demander_texte(
            "Normalisation du pic",
            f"Niveau de pic cible pour les {len(self.playlist)} fichier(s), "
            "en dBFS\n(0 = saturation, valeur négative = marge ; défaut -3) :\n\n"
            "• Les originaux sont conservés (sortie « _norm.wav »).\n"
            "• Les repères sont copiés sur les nouveaux fichiers.\n"
            "• Tous les morceaux auront un niveau de pic homogène.",
            defaut="-3")
        if saisie is None:
            return   # annulé
        cible = self._parser_niveau_db(saisie)
        if cible is None:
            self._info("Valeur invalide",
                       "Entre un niveau en dB entre -60 et 0 (ex. -3).",
                       accent=ROUGE)
            return
        self._cible_normalisation = cible
        self.bouton_normaliser.config(state="disabled")
        fichiers = list(enumerate(self.playlist))
        threading.Thread(target=self._normaliser_thread,
                         args=(fichiers, cible), daemon=True).start()

    @staticmethod
    def _parser_niveau_db(texte):
        """Lit un niveau en dB (ex. « -3 », « -3.5 », « -3 dB »).

        Renvoie un float dans [-60, 0], ou None si invalide.
        """
        m = re.search(r"-?\d+(?:[.,]\d+)?", texte or "")
        if not m:
            return None
        valeur = float(m.group(0).replace(",", "."))
        if not -60.0 <= valeur <= 0.0:
            return None
        return valeur

    def _normaliser_thread(self, fichiers, cible):
        total = len(fichiers)
        echecs = 0
        for rang, (index, chemin) in enumerate(fichiers, start=1):
            self.racine.after(0, lambda r=rang, t=total:
                              self.bouton_normaliser.config(
                                  text=f"Normalisation {r}/{t}…"))
            sortie = os.path.splitext(chemin)[0] + "_norm.wav"
            try:
                gain = self._gain_normalisation(chemin, cible)
                if gain is None:
                    echecs += 1
                    continue
                subprocess.run(
                    [self.ffmpeg, "-v", "quiet", "-y", "-i", chemin,
                     "-af", f"volume={gain:.2f}dB", "-c:a", "pcm_s16le", sortie],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    check=True)
            except Exception:
                echecs += 1
                continue
            _, duree = self._infos_fichier(sortie)
            self.racine.after(0, lambda i=index, c=chemin, s=sortie, d=duree:
                              self._remplacer_par_wav(i, c, s, d))
        self.racine.after(0, lambda: self._fin_normalisation(echecs, total))

    def _gain_normalisation(self, chemin, cible=-3.0):
        """Gain (dB) à appliquer pour amener le pic du fichier à `cible` dBFS.

        Renvoie None si le pic n'a pas pu être mesuré.
        """
        res = subprocess.run(
            [self.ffmpeg, "-hide_banner", "-i", chemin,
             "-af", "volumedetect", "-f", "null", "-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        texte = res.stderr.decode("utf-8", "ignore")
        m = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", texte)
        if not m:
            return None
        return cible - float(m.group(1))   # pic actuel -> cible

    def _fin_normalisation(self, echecs, total):
        self.bouton_normaliser.config(state="normal", text=self._texte_normaliser)
        cible = getattr(self, "_cible_normalisation", -3.0)
        message = (f"{total - echecs}/{total} fichier(s) "
                   f"normalisés à {cible:g} dB.")
        if echecs:
            message += f"\n\n{echecs} fichier(s) en échec."
        self._info("Normalisation terminée", message,
                   accent=ROUGE if echecs else VERT)

    def _charger_infos_liste(self, elements):
        """Lit la durée de chaque fichier ajouté (pour le temps total cumulé)."""
        for index, chemin in elements:
            _, duree = self._infos_fichier(chemin)
            self.racine.after(0, lambda i=index, c=chemin, d=duree:
                              self._maj_duree_liste(i, c, d))

    def _maj_duree_liste(self, index, chemin, duree):
        """Enregistre la durée d'une piste et met à jour le temps total."""
        if not (0 <= index < len(self.playlist)) or self.playlist[index] != chemin:
            return  # la playlist a changé entre-temps
        if index < len(self.durees):
            self.durees[index] = duree
            self._maj_temps_total()
            self.liste.delete(index)
            self.liste.insert(index, self._libelle_liste(chemin, duree))
            self._surligner_courant()

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

    def _libelle_liste(self, chemin, duree):
        """Texte d'une ligne de playlist : nom du fichier + durée (si connue)."""
        nom = os.path.basename(chemin)
        if duree > 0:
            return f"{nom}   [{self._formate_total(duree)}]"
        return nom

    def _autoriser_changement(self, index):
        """Pendant la lecture, demande confirmation avant de changer de morceau.

        Renvoie True si on peut continuer (rien en lecture, ou l'utilisateur
        confirme), False sinon. Sur refus, on remet le surlignage sur la piste
        en cours pour que la sélection ne bouge pas. La réponse est mémorisée
        ~0,6 s : un double-clic (qui émet simple + double clic sur le même
        morceau) ne montre ainsi qu'une seule boîte de dialogue.
        """
        if not self.player.is_playing():
            return True
        maintenant = time.perf_counter()
        t, idx, reponse = self._derniere_confirmation
        if idx == index and maintenant - t < 0.6:
            return reponse   # même morceau, clic très rapproché : on réutilise
        nom = os.path.basename(self.playlist[index])
        reponse = self._demander_oui_non(
            "Changer de morceau",
            f"Une lecture est en cours.\n\nL'interrompre et passer à :\n{nom} ?")
        self._derniere_confirmation = (time.perf_counter(), index, reponse)
        if not reponse:
            self._surligner_courant()   # annule le changement de sélection
        return reponse

    def _clic_liste(self, evenement):
        """Vrai clic souris sur un fichier : on affiche sa courbe (sans lire)."""
        index = self.liste.nearest(evenement.y)
        if 0 <= index < len(self.playlist) and index != self.index_courant:
            if self._autoriser_changement(index):
                self._lire_index(index, lire=False)

    def _lire_selection(self, evenement=None):
        selection = self.liste.curselection()
        if not selection:
            return
        index = selection[0]
        if index != self.index_courant and not self._autoriser_changement(index):
            return
        self._lire_index(index)

    def _surligner_courant(self):
        """Force le surlignage de la liste sur la piste réellement courante.

        Appelé à chaque (re)lecture : même si le surlignage a glissé entre-temps
        (timing VLC/Tk en fin de morceau), la ligne en vert correspond toujours
        à `index_courant`, donc à la musique qui joue.
        """
        if 0 <= self.index_courant < self.liste.size():
            self.liste.selection_clear(0, "end")
            self.liste.selection_set(self.index_courant)
            self.liste.see(self.index_courant)

    def _lire_index(self, index, lire=True):
        if 0 <= index < len(self.playlist):
            self.index_courant = index
            # On réinitialise le lecteur avant de changer de média : sinon, si le
            # morceau précédent était terminé (état Ended), un play() ultérieur
            # (ex. barre espace) rejouerait l'ancien morceau.
            self.player.stop()
            media = self.instance.media_new(self.playlist[index])
            self.player.set_media(media)
            self.horloge_reference = None   # nouvelle piste : repartir propre
            self.duree_actuelle = 0         # durée recalculée par le thread infos
            self.position_precedente = 0    # remet à zéro la détection des repères
            self._derniere_position = 0     # nouvelle piste : onde lue repart à 0
            self.position_demandee = None   # oublie une position cliquée précédente
            self._annuler_alerte()          # stoppe un décompte/alerte en cours
            self.label_titre.config(text=os.path.basename(self.playlist[index]))
            # Surligne la piste en cours dans la liste
            self._surligner_courant()
            # Lance le calcul de la forme d'onde + lecture des infos (sans bloquer)
            self._lancer_forme_onde(self.playlist[index])
            self._lancer_infos(self.playlist[index])
            self._maj_boutons_reperes()   # boutons d'accès rapide de cette piste
            self._maj_boutons_boucles()
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
        # Anti-rebond : un même appui peut générer deux évènements rapprochés
        # (ex. barre espace + activation d'un bouton ayant le focus). On ignore
        # le second appel à moins de 0,2 s pour éviter un play suivi d'un pause.
        maintenant = time.perf_counter()
        if maintenant - self._dernier_play_pause < 0.2:
            return
        self._dernier_play_pause = maintenant
        if not self.playlist:
            return
        if self.index_courant == -1:
            self._lire_index(0)
            return
        if self.player.is_playing():
            self.player.pause()
            self.bouton_play.config(text="▶")
        elif self.player.get_state() == vlc.State.Paused:
            # Reprise depuis la pause : on conserve la position.
            self.player.play()
            self.bouton_play.config(text="⏸")
            self._surligner_courant()
        else:
            # Arrêté / terminé / chargé sans lecture : on (re)charge
            # explicitement le média de la piste courante avant de jouer,
            # pour ne jamais rejouer l'ancien morceau resté en état Ended.
            self.player.stop()
            self.player.set_media(
                self.instance.media_new(self.playlist[self.index_courant]))
            self.player.play()
            self.horloge_reference = None
            self.bouton_play.config(text="⏸")
            # La liste suit toujours la piste qui démarre réellement.
            self._surligner_courant()

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
    #  Cartouchier : sons déclenchés au clavier, par-dessus la playlist
    # ------------------------------------------------------------------ #
    def _construire_cartouchier(self, parent):
        """Panneau du cartouchier (colonne du milieu de la zone centrale)."""
        cadre = tk.Frame(parent, bg=FOND_CLAIR)
        cadre.pack(side="left", fill="y", padx=(12, 0))
        cadre_titre = tk.Frame(cadre, bg=FOND_CLAIR)
        cadre_titre.pack(fill="x", anchor="w", padx=self._e(8), pady=(self._e(8), 0))
        tk.Label(cadre_titre, text="🎛 Cartouchier", bg=FOND_CLAIR, fg=TEXTE,
                 font=("Segoe UI", self._e(13), "bold")).pack(side="left")
        self._creer_bouton(cadre_titre, "🗑 Vider", commande=self.vider_cartouchier,
                  taille=9, padx=6, pady=2).pack(side="left", padx=(10, 0))
        tk.Label(cadre, text="Une touche du clavier lance le son (par-dessus la "
                 "musique).\nClic droit sur un cart pour le modifier.",
                 bg=FOND_CLAIR, fg="#9e9e9e", justify="left",
                 font=("Segoe UI", self._e(9))).pack(anchor="w", padx=self._e(8),
                 pady=(0, self._e(6)))
        # Grille des carts (reconstruite à chaque changement par _render_carts).
        self.cadre_grille_carts = tk.Frame(cadre, bg=FOND_CLAIR)
        self.cadre_grille_carts.pack(fill="both", expand=True, padx=self._e(8),
                                     pady=(0, self._e(8)))

    def _style_cart(self):
        return dict(bg=FOND_CART, fg=TEXTE, activebackground="#606060",
                    activeforeground=TEXTE, relief="flat", borderwidth=0,
                    highlightthickness=0, width=20, height=3, justify="center",
                    cursor="hand2", takefocus=0, wraplength=self._e(190),
                    font=("Segoe UI", self._e(14)))

    def _afficher_touche(self, keysym):
        """Nom lisible d'une touche (keysym Tk) pour l'affichage des carts."""
        if not keysym:
            return ""
        speciales = {"Return": "Entrée", "Tab": "Tab", "BackSpace": "Retour",
                     "Delete": "Suppr", "Insert": "Inser", "Up": "↑", "Down": "↓",
                     "Left": "←", "Right": "→", "Prior": "PgPréc", "Next": "PgSuiv",
                     "Home": "Début", "End": "Fin", "plus": "+", "minus": "-",
                     "comma": ",", "period": ".", "slash": "/", "asterisk": "*",
                     "semicolon": ";", "Caps_Lock": "Maj", "Menu": "Menu"}
        if keysym in speciales:
            return speciales[keysym]
        if keysym.startswith("KP_"):
            reste = self._afficher_touche(keysym[3:]) if keysym[3:] else ""
            return ("Num " + reste).strip()
        if len(keysym) == 1:
            return keysym.upper()
        return keysym

    def _libelle_cart(self, cart):
        """Texte affiché sur le bouton d'un cart (touche + nom)."""
        touche = self._afficher_touche(cart.get("touche"))
        tete = f"[{touche}]" if touche else "[touche ?]"
        return f"{tete}\n{cart.get('nom', '(sans nom)')}"

    def _render_carts(self):
        """(Re)construit la grille des carts d'après self.carts."""
        for w in self.cadre_grille_carts.winfo_children():
            w.destroy()
        self._cart_boutons = []
        cols = 3
        style = self._style_cart()
        for i, cart in enumerate(self.carts):
            conteneur = tk.Frame(self.cadre_grille_carts, bg=FOND_CART)
            conteneur.grid(row=i // cols, column=i % cols,
                           padx=self._e(5), pady=self._e(5))
            bouton = tk.Button(conteneur, text=self._libelle_cart(cart),
                               command=lambda i=i: self._jouer_cart(i), **style)
            bouton.bind("<Button-3>", lambda e, i=i: self._menu_cart(e, i))
            bouton.pack()
            curseur = ttk.Scale(conteneur, from_=0, to=100, orient="horizontal",
                                style="Cart.Horizontal.TScale",
                                command=lambda v, i=i: self._sur_volume_cart(i, v))
            curseur.set(cart.get("volume", 100))
            curseur.pack(fill="x", padx=self._e(4), pady=(0, self._e(4)))
            curseur.bind("<ButtonRelease-1>", lambda e: self._sauver_carts())
            self._cart_boutons.append(bouton)
        # Bouton « ＋ » pour ajouter un cart, à la suite de la grille.
        n = len(self.carts)
        plus = tk.Button(self.cadre_grille_carts, text="＋\nAjouter",
                         command=self._ajouter_cart, **style)
        plus.config(fg="#9e9e9e")
        plus.grid(row=n // cols, column=n % cols,
                  padx=self._e(5), pady=self._e(5))
        # La barre espace reste play/pause même si un cart a le focus (clic souris).
        self._neutraliser_espace(self.cadre_grille_carts)

    def _sur_volume_cart(self, i, valeur):
        """Applique en direct le volume propre à un cart (glissière de sa carte)."""
        if not (0 <= i < len(self.carts)):
            return
        v = int(float(valeur))
        self.carts[i]["volume"] = v
        joueur = self._cart_players.get(self.carts[i]["uid"])
        if joueur is not None:
            joueur.audio_set_volume(v)

    def _flash_cart(self, i, couleur=VERT):
        """Surligne brièvement un cart pour confirmer le déclenchement."""
        if not (0 <= i < len(self._cart_boutons)):
            return
        bouton = self._cart_boutons[i]
        bouton.config(bg=couleur, fg=FOND)

        def restaurer():
            try:
                if bouton.winfo_exists():
                    bouton.config(bg=FOND_CART, fg=TEXTE)
            except tk.TclError:
                pass
        self.racine.after(170, restaurer)

    def _maj_surbrillance_carts(self):
        """Garde en surbrillance chaque cart dont le son est en cours de lecture."""
        for i, bouton in enumerate(getattr(self, "_cart_boutons", [])):
            if not (0 <= i < len(self.carts)):
                continue
            joueur = self._cart_players.get(self.carts[i]["uid"])
            en_lecture = joueur is not None and joueur.is_playing()
            fond = VERT if en_lecture else FOND_CART
            if bouton.cget("bg") != fond:
                bouton.config(bg=fond, fg=FOND if en_lecture else TEXTE)

    def _jouer_cart(self, i):
        """Déclenche le cart i : lecture par-dessus la musique, depuis le début.

        Chaque cart a son propre lecteur VLC : plusieurs carts peuvent sonner en
        même temps. Si le cart joue déjà, ré-appuyer sur sa touche l'arrête
        (bascule lecture / arrêt).
        """
        if not (0 <= i < len(self.carts)):
            return
        cart = self.carts[i]
        chemin = cart.get("chemin")
        if not chemin or not os.path.exists(chemin):
            self._flash_cart(i, ROUGE)   # fichier introuvable
            return
        player = self._cart_players.get(cart["uid"])
        if player is None:
            player = self.instance.media_player_new()
            self._cart_players[cart["uid"]] = player
        elif player.is_playing():
            player.stop()                # déjà en lecture : on coupe le son
            return
        player.stop()
        player.set_media(self.instance.media_new(chemin))
        player.audio_set_volume(int(cart.get("volume", 100)))
        player.play()

    def _arreter_cart(self, i):
        """Stoppe le son d'un cart en cours de lecture."""
        if 0 <= i < len(self.carts):
            player = self._cart_players.get(self.carts[i]["uid"])
            if player is not None:
                player.stop()

    def _touche_cart(self, evenement):
        """Toute touche du clavier déclenche le cart qui lui est associé.

        Ignorée pendant une saisie (champ « Aller à »). La barre espace garde son
        rôle de lecture/pause (binding plus spécifique, jamais reçu ici).
        """
        if isinstance(self.racine.focus_get(), tk.Entry):
            return
        keysym = evenement.keysym
        cle = keysym.lower() if len(keysym) == 1 else keysym
        for i, cart in enumerate(self.carts):
            touche = cart.get("touche")
            if touche and touche == cle:
                self._jouer_cart(i)
                return "break"

    def _menu_cart(self, evenement, i):
        """Menu contextuel (clic droit) d'un cart."""
        if not (0 <= i < len(self.carts)):
            return
        menu = tk.Menu(self.racine, tearoff=0, bg=FOND_CLAIR, fg=TEXTE,
                       activebackground=VERT, activeforeground=FOND,
                       borderwidth=0, font=("Segoe UI", self._e(11)))
        menu.add_command(label="▶  Jouer", command=lambda: self._jouer_cart(i))
        menu.add_command(label="⏹  Arrêter", command=lambda: self._arreter_cart(i))
        menu.add_separator()
        menu.add_command(label="✎  Renommer", command=lambda: self._renommer_cart(i))
        menu.add_command(label="⌨  Changer la touche",
                         command=lambda: self._changer_touche_cart(i))
        menu.add_command(label="🎵  Remplacer le fichier",
                         command=lambda: self._remplacer_cart(i))
        menu.add_separator()
        menu.add_command(label="🗑  Retirer", command=lambda: self._retirer_cart(i))
        try:
            menu.tk_popup(evenement.x_root, evenement.y_root)
        finally:
            menu.grab_release()

    def _affecter_touche(self, i, touche):
        """Associe une touche au cart i (en la libérant d'un éventuel autre cart)."""
        if touche:
            for j, autre in enumerate(self.carts):
                if j != i and autre.get("touche") == touche:
                    autre["touche"] = None
        self.carts[i]["touche"] = touche

    def _ajouter_cart(self):
        """Crée un nouveau cart : choix du fichier, du nom, puis de la touche."""
        chemin = filedialog.askopenfilename(
            title="Choisir un son pour le cart",
            filetypes=[("Audio", "*.mp3 *.wav *.flac *.ogg *.m4a *.aac"),
                       ("Tous les fichiers", "*.*")])
        if not chemin:
            return
        nom = os.path.splitext(os.path.basename(chemin))[0]
        annule, touche = self._capturer_touche()
        self.carts.append({"uid": self._prochain_uid(), "nom": nom,
                           "chemin": chemin, "touche": None, "volume": 100})
        self._affecter_touche(len(self.carts) - 1, None if annule else touche)
        self._sauver_carts()
        self._render_carts()

    def _renommer_cart(self, i):
        if not (0 <= i < len(self.carts)):
            return
        nom = self._demander_texte("Renommer le cart", "Nom du cart :",
                                   defaut=self.carts[i].get("nom", ""))
        if nom is not None and nom.strip():
            self.carts[i]["nom"] = nom.strip()
            self._sauver_carts()
            self._render_carts()

    def _changer_touche_cart(self, i):
        if not (0 <= i < len(self.carts)):
            return
        annule, touche = self._capturer_touche(self.carts[i].get("touche"),
                                               index_exclu=i)
        if not annule:
            self._affecter_touche(i, touche)
            self._sauver_carts()
            self._render_carts()

    def _remplacer_cart(self, i):
        if not (0 <= i < len(self.carts)):
            return
        chemin = filedialog.askopenfilename(
            title="Choisir un autre son pour le cart",
            filetypes=[("Audio", "*.mp3 *.wav *.flac *.ogg *.m4a *.aac"),
                       ("Tous les fichiers", "*.*")])
        if not chemin:
            return
        self._arreter_cart(i)
        self.carts[i]["chemin"] = chemin
        self._sauver_carts()
        self._render_carts()

    def _retirer_cart(self, i):
        if not (0 <= i < len(self.carts)):
            return
        if not self._demander_oui_non(
                "Retirer le cart",
                f"Retirer ce cart du cartouchier ?\n\n{self.carts[i].get('nom', '')}",
                oui="Retirer", accent=ROUGE):
            return
        uid = self.carts[i]["uid"]
        player = self._cart_players.pop(uid, None)
        if player is not None:
            try:
                player.stop()
                player.release()
            except Exception:
                pass
        del self.carts[i]
        self._sauver_carts()
        self._render_carts()

    def vider_cartouchier(self):
        """Retire tous les carts du cartouchier. Les fichiers audio ne sont pas
        supprimés du disque : on ne vide que le cartouchier lui-même."""
        if not self.carts:
            return
        if not self._demander_oui_non(
                "Vider le cartouchier",
                f"Retirer les {len(self.carts)} cart(s) du cartouchier ?\n\n"
                "Les fichiers audio ne sont pas supprimés du disque.",
                oui="Vider", accent=ROUGE):
            return
        for player in self._cart_players.values():
            try:
                player.stop()
                player.release()
            except Exception:
                pass
        self._cart_players.clear()
        self.carts.clear()
        self._sauver_carts()
        self._render_carts()

    def _capturer_touche(self, actuelle=None, index_exclu=None):
        """Dialogue « appuyez sur une touche ». Renvoie (annule, keysym|None).

        annule=True si l'utilisateur a fermé sans choisir (Échap). La barre espace,
        F11 et Échap sont réservés (lecture / plein écran / annulation). Si la
        touche est déjà prise par un autre cart, on prévient et on attend une
        seconde pression de confirmation avant de la lui retirer.
        """
        resultat = {"annule": True, "valeur": None}
        en_attente = {"cle": None}   # touche déjà prise, en attente de confirmation
        top, cadre = self._creer_dialogue("Touche du cart", VERT)
        info = tk.Label(cadre, text="Appuyez sur la touche à associer à ce cart.\n"
                        "(Échap pour annuler)", bg=FOND_CLAIR, fg=TEXTE,
                        justify="left", font=("Segoe UI", self._e(12)))
        info.pack(anchor="w", padx=self._e(24), pady=(0, self._e(10)))
        apercu = tk.Label(cadre, text=self._afficher_touche(actuelle) or "—",
                          bg=FOND, fg=VERT, font=("Consolas", self._e(20), "bold"))
        apercu.pack(fill="x", padx=self._e(24), ipady=self._e(8),
                    pady=(0, self._e(20)))

        def cart_avec_touche(cle):
            for j, autre in enumerate(self.carts):
                if j != index_exclu and autre.get("touche") == cle:
                    return autre
            return None

        def on_key(evenement):
            keysym = evenement.keysym
            if keysym == "Escape":
                top.destroy()
                return "break"
            if keysym in ("space", "F11"):
                en_attente["cle"] = None
                info.config(text="Touche réservée (lecture / plein écran).\n"
                            "Choisis-en une autre.", fg=ROUGE)
                return "break"
            cle = keysym.lower() if len(keysym) == 1 else keysym
            autre = cart_avec_touche(cle)
            if autre and en_attente["cle"] != cle:
                en_attente["cle"] = cle
                nom = autre.get("nom") or "un autre cart"
                info.config(text=f"Touche déjà utilisée par « {nom} ».\n"
                            "Ré-appuie pour la lui retirer, ou choisis-en une autre.",
                            fg=ROUGE)
                apercu.config(text=self._afficher_touche(keysym))
                return "break"
            resultat["annule"] = False
            resultat["valeur"] = cle
            top.destroy()
            return "break"

        top.bind("<KeyPress>", on_key)
        self._placer_dialogue(top)
        self.racine.wait_window(top)
        return resultat["annule"], resultat["valeur"]

    # ------------------------------------------------------------------ #
    #  Persistance du cartouchier (carts.json, à côté de l'exe)
    # ------------------------------------------------------------------ #
    def _chemin_carts(self):
        if getattr(sys, "frozen", False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, "carts.json")

    def _charger_carts(self):
        """Lit carts.json. Chaque cart reçoit un uid stable (pour son lecteur)."""
        try:
            with open(self._chemin_carts(), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = []
        carts = []
        uid_max = 0
        for c in data if isinstance(data, list) else []:
            if not isinstance(c, dict):
                continue
            uid = c.get("uid")
            if not isinstance(uid, int):
                uid_max += 1
                uid = uid_max
            else:
                uid_max = max(uid_max, uid)
            volume = c.get("volume", 100)
            if not isinstance(volume, (int, float)):
                volume = 100
            carts.append({"uid": uid, "nom": c.get("nom", "(sans nom)"),
                          "chemin": c.get("chemin", ""),
                          "touche": c.get("touche") or None,
                          "volume": max(0, min(100, int(volume)))})
        self._cart_uid = uid_max
        return carts

    def _sauver_carts(self):
        data = [{"uid": c["uid"], "nom": c["nom"], "chemin": c["chemin"],
                 "touche": c.get("touche"), "volume": c.get("volume", 100)}
                for c in self.carts]
        try:
            with open(self._chemin_carts(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _prochain_uid(self):
        self._cart_uid = getattr(self, "_cart_uid", 0) + 1
        return self._cart_uid

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
                self._maj_compteurs(position_ms, duree)

    def _fin_glissement(self, evenement):
        # Au relâché, on applique la nouvelle position à la lecture
        if self.player.get_length() > 0:
            self.player.set_position(float(self.barre.get()) / 1000)
        self.horloge_reference = None   # forcer une resynchro après le saut
        self.utilisateur_glisse = False

    # ------------------------------------------------------------------ #
    #  Forme d'onde (waveform)
    # ------------------------------------------------------------------ #
    def _pixel_a_fraction(self, x):
        """Position pixel sur le canvas -> fraction de piste (0..1), selon le zoom."""
        largeur = self.canvas_onde.winfo_width()
        if largeur <= 0:
            return 0.0
        return self.zoom_debut + (x / largeur) * (self.zoom_fin - self.zoom_debut)

    def _fraction_au_pixel(self, fraction):
        """Fraction de piste (0..1) -> position pixel sur le canvas, selon le zoom."""
        span = self.zoom_fin - self.zoom_debut
        if span <= 0:
            return 0.0
        return (fraction - self.zoom_debut) / span * self.canvas_onde.winfo_width()

    def _zoom_onde(self, evenement):
        """Molette : zoome/dézoome la forme d'onde autour du curseur souris."""
        if self.forme_onde is None or self.canvas_onde.winfo_width() <= 1:
            return "break"
        ancre = self._pixel_a_fraction(evenement.x)        # point fixe du zoom
        span = self.zoom_fin - self.zoom_debut
        facteur = 0.8 if evenement.delta > 0 else 1.25     # molette haut = zoom avant
        nouveau_span = max(0.001, min(1.0, span * facteur))
        # On garde le point sous le curseur à la même place à l'écran.
        ratio = (ancre - self.zoom_debut) / span if span > 0 else 0.5
        debut = ancre - ratio * nouveau_span
        fin = debut + nouveau_span
        if debut < 0:
            debut, fin = 0.0, nouveau_span
        if fin > 1:
            fin, debut = 1.0, 1.0 - nouveau_span
        self.zoom_debut, self.zoom_fin = max(0.0, debut), min(1.0, fin)
        self._dessiner_onde()
        duree = self._duree_courante()
        if duree > 0:
            self._maj_curseur_onde(self._derniere_position, duree)
        return "break"

    def _lancer_forme_onde(self, chemin):
        """Vide la courbe et démarre le décodage de la piste en arrière-plan."""
        self.forme_onde = None
        self.zoom_debut, self.zoom_fin = 0.0, 1.0   # nouvelle piste : zoom à fond
        self.canvas_onde.delete("all")
        self._id_curseur = None       # curseur effacé : à recréer
        self._cols_onde = []          # traits de l'onde effacés
        self._px_joue_courant = 0
        self.token_onde += 1
        token = self.token_onde
        threading.Thread(target=self._decoder_forme_onde,
                         args=(chemin, token), daemon=True).start()

    def _decoder_forme_onde(self, chemin, token, resolution=400000):
        """Décode l'audio en stéréo via ffmpeg et calcule l'enveloppe des deux
        voies (gauche / droite) séparément.

        Tourne dans un thread : à la fin, le tracé est replanifié sur le thread
        principal de tkinter (seul autorisé à toucher aux widgets). La résolution
        élevée (beaucoup de points) garde une forme d'onde nette même très zoomée.
        Renvoie un tableau (2, n) : ligne 0 = voie gauche, ligne 1 = voie droite
        (un fichier mono est dupliqué en deux voies identiques par ffmpeg).
        """
        try:
            # ffmpeg -> PCM 16 bits, stéréo entrelacé, 44,1 kHz, sur la sortie std.
            # 44,1 kHz : enveloppe très fine, pour un zoom maximal net.
            commande = [self.ffmpeg, "-v", "quiet", "-i", chemin,
                        "-ac", "2", "-ar", "44100", "-f", "s16le", "-"]
            brut = subprocess.run(
                commande, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            echantillons = np.frombuffer(brut, dtype=np.int16)
            if echantillons.size < 2:
                return
            if echantillons.size % 2:           # sécurité : nombre impair
                echantillons = echantillons[:-1]
            paires = echantillons.reshape(-1, 2)    # colonnes : [gauche, droite]
            voies = []
            for canal in range(2):
                amplitudes = np.abs(paires[:, canal].astype(np.float32))
                # Réduction en colonnes de crête couvrant TOUT le fichier (sans
                # rien tronquer, sinon la courbe « manque » sa fin et le curseur,
                # calé sur la durée totale, prend un retard croissant).
                n_cols = min(resolution, amplitudes.size)
                bornes = np.linspace(0, amplitudes.size, n_cols,
                                     endpoint=False).astype(np.intp)
                voies.append(np.maximum.reduceat(amplitudes, bornes))
            # Normalisation commune aux deux voies (préserve l'équilibre G/D)
            maxi = max(float(voies[0].max()), float(voies[1].max()))
            if maxi > 0:
                voies = [v / maxi for v in voies]
            n = min(voies[0].shape[0], voies[1].shape[0])
            cretes = np.stack([voies[0][:n], voies[1][:n]])
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
        """Trace les deux voies (gauche en haut, droite en bas) sur le canvas."""
        c = self.canvas_onde
        c.delete("all")
        self._id_curseur = None       # curseur effacé : à recréer
        self._cols_onde = []          # ids des traits par colonne : à reconstruire
        self._px_joue_courant = 0     # bord « déjà lu » (en pixels) recoloré
        cretes = self.forme_onde
        largeur = c.winfo_width()
        hauteur = c.winfo_height()
        if cretes is None or largeur <= 1:
            return
        gauche, droite = cretes[0], cretes[1]
        n = gauche.shape[0]
        span = self.zoom_fin - self.zoom_debut
        axe_g = hauteur * 0.25       # axe horizontal de la voie gauche (haut)
        axe_d = hauteur * 0.75       # axe horizontal de la voie droite (bas)
        demi = hauteur * 0.25 - 2    # amplitude max d'une voie
        # Bord « déjà lu » à recolorer d'emblée (utile après zoom/défilement en
        # cours de lecture) : les colonnes à gauche de ce pixel naissent en rouge.
        duree = self._duree_courante()
        bord_joue = self._fraction_au_pixel(self._derniere_position / duree) \
            if duree > 0 else 0
        for x in range(largeur):
            # Plage de crêtes couverte par ce pixel (selon le zoom) : on prend
            # le maximum pour ne manquer aucune pointe, zoomé comme dézoomé.
            f0 = self.zoom_debut + (x / largeur) * span
            f1 = self.zoom_debut + ((x + 1) / largeur) * span
            i0 = min(n - 1, max(0, int(f0 * n)))
            i1 = min(n, max(i0 + 1, int(f1 * n)))
            ag = float(gauche[i0:i1].max()) * demi
            ad = float(droite[i0:i1].max()) * demi
            couleur = VERT_JOUE if x < bord_joue else VERT
            idg = c.create_line(x, axe_g - ag, x, axe_g + ag, fill=couleur,
                                tags="onde")
            idd = c.create_line(x, axe_d - ad, x, axe_d + ad, fill=couleur,
                                tags="onde")
            self._cols_onde.append((idg, idd))
        self._px_joue_courant = max(0, min(largeur, int(bord_joue)))
        # Séparateur central + libellés des voies
        c.create_line(0, hauteur / 2, largeur, hauteur / 2,
                      fill=SURVOL_CLAIR, tags="onde")
        c.create_text(4, 2, anchor="nw", text="G", fill="#9e9e9e",
                      font=("Segoe UI", self._e(9)), tags="onde")
        c.create_text(4, hauteur / 2 + 2, anchor="nw", text="D", fill="#9e9e9e",
                      font=("Segoe UI", self._e(9)), tags="onde")
        self._dessiner_reperes()
        self._dessiner_partie_jouee()   # teinte le fond déjà lu (sous la courbe)
        self._dessiner_selection()      # zone d'édition sélectionnée (par-dessus)

    def _dessiner_partie_jouee(self):
        """Recolore en rouge les traits de l'onde déjà lus (jauge de progression).

        Au lieu de poser un grand rectangle redessiné à chaque image — qui
        forçait Tk à repeindre tous les traits recouverts (coût croissant avec
        la position) — on ne retouche que les quelques colonnes franchies depuis
        la dernière image : coût constant, indépendant de l'avancement.
        """
        c = self.canvas_onde
        if self.forme_onde is None or not self._cols_onde:
            return
        duree = self._duree_courante()
        if duree <= 0:
            return
        largeur = len(self._cols_onde)
        # Bord « déjà lu » en pixels dans la vue courante (selon le zoom).
        bord = max(0, min(largeur,
                          int(self._fraction_au_pixel(
                              self._derniere_position / duree))))
        if bord == self._px_joue_courant:
            return   # aucune colonne franchie depuis la dernière image
        if bord > self._px_joue_courant:
            # On avance : les nouvelles colonnes passent au rouge.
            for x in range(self._px_joue_courant, bord):
                idg, idd = self._cols_onde[x]
                c.itemconfig(idg, fill=VERT_JOUE)
                c.itemconfig(idd, fill=VERT_JOUE)
        else:
            # On recule (clic arrière, boucle A-B) : retour au vert.
            for x in range(bord, self._px_joue_courant):
                idg, idd = self._cols_onde[x]
                c.itemconfig(idg, fill=VERT)
                c.itemconfig(idd, fill=VERT)
        self._px_joue_courant = bord

    # ------------------------------------------------------------------ #
    #  Sélection d'une zone sur la forme d'onde (pour l'édition)
    # ------------------------------------------------------------------ #
    def _debut_selection(self, evenement):
        """Shift + clic : démarre une sélection (mémorise le début)."""
        duree = self._duree_courante()
        if self.forme_onde is None or duree <= 0:
            return "break"
        fraction = max(0.0, min(1.0, self._pixel_a_fraction(evenement.x)))
        self.sel_a = fraction * duree
        self.sel_b = self.sel_a
        self._dessiner_selection()
        self._maj_label_selection()
        return "break"

    def _glisser_selection(self, evenement):
        """Shift + glisser : étend la sélection jusqu'au pointeur."""
        duree = self._duree_courante()
        if self.sel_a is None or duree <= 0:
            return "break"
        fraction = max(0.0, min(1.0, self._pixel_a_fraction(evenement.x)))
        self.sel_b = fraction * duree
        self._dessiner_selection()
        self._maj_label_selection()
        return "break"

    def _fin_selection(self, evenement):
        """Fin du glissé : ordonne début/fin ; annule si la zone est nulle."""
        if self.sel_a is None or self.sel_b is None:
            return "break"
        a, b = sorted((self.sel_a, self.sel_b))
        if b - a < 1:            # simple clic Shift sans glisser : pas de zone
            self.sel_a = self.sel_b = None
        else:
            self.sel_a, self.sel_b = a, b
        self._dessiner_selection()
        self._maj_label_selection()
        return "break"

    def _effacer_selection(self):
        """Annule la sélection courante (et la boucle A-B qui en dépend)."""
        self.sel_a = self.sel_b = None
        if self.boucle_ab.get():
            self.boucle_ab.set(False)
        self._dessiner_selection()
        self._maj_label_selection()

    def _basculer_boucle_ab(self):
        """Active/désactive la boucle A-B (sur la zone sélectionnée).

        À l'activation, exige une sélection valide (A→B) ; sinon on prévient et
        on décoche. Si on lit déjà au-delà de B, on revient aussitôt à A.
        """
        if not self.boucle_ab.get():
            return
        if self.sel_a is None or self.sel_b is None or \
                abs(self.sel_b - self.sel_a) < 1:
            self.boucle_ab.set(False)
            self._info("Boucle A-B",
                       "Sélectionne d'abord une zone (Shift + glisser sur la "
                       "courbe) : elle sert de points A et B.", accent=ROUGE)
            return
        a = min(self.sel_a, self.sel_b)
        if self.player.is_playing() and self.player.get_time() >= max(
                self.sel_a, self.sel_b):
            self.player.set_time(int(a))
            self.dernier_temps_vlc = int(a)
            self.temps_reference = a
            self.horloge_reference = time.perf_counter()

    def _maj_label_selection(self):
        """Affiche la plage sélectionnée à côté des boutons d'édition."""
        if self.sel_a is None or self.sel_b is None:
            self.label_selection.config(text="")
            return
        a, b = sorted((self.sel_a, self.sel_b))
        self.label_selection.config(
            text=f"{formate_temps(int(a))} → {formate_temps(int(b))} "
                 f"({formate_temps(int(b - a))})")

    def _boucles_courantes(self):
        """Liste des boucles A-B enregistrées pour la piste en cours (créée
        si absente)."""
        if self.index_courant < 0:
            return []
        return self.boucles.setdefault(self.playlist[self.index_courant], [])

    def _sauver_boucle_ab(self):
        """Enregistre la sélection courante comme nouvelle boucle nommée,
        pour pouvoir en garder plusieurs différentes sur la même piste."""
        if self.index_courant < 0:
            return
        if self.sel_a is None or self.sel_b is None or \
                abs(self.sel_b - self.sel_a) < 1:
            self._info("Sauver boucle",
                       "Sélectionne d'abord une zone (Shift + glisser sur la "
                       "courbe) avant de l'enregistrer.", accent=ROUGE)
            return
        a, b = sorted((self.sel_a, self.sel_b))
        nom = self._demander_texte(
            "Sauver la boucle",
            f"Nom de la boucle ({formate_temps(int(a))} → "
            f"{formate_temps(int(b))}) :")
        if not nom:
            return
        boucles = self._boucles_courantes()
        boucles.append({"a": a, "b": b, "nom": nom.strip()})
        self._sauver_boucles()
        self._maj_boutons_boucles()

    def _activer_boucle(self, boucle):
        """Charge une boucle enregistrée comme sélection A-B et l'active."""
        self.sel_a, self.sel_b = boucle["a"], boucle["b"]
        self._dessiner_selection()
        self._maj_label_selection()
        self.boucle_ab.set(True)
        self._basculer_boucle_ab()

    def _supprimer_boucle(self, evenement, boucle):
        """Menu contextuel (clic droit) d'une boucle : proposer sa suppression."""
        menu = tk.Menu(self.racine, tearoff=0, bg=FOND_CLAIR, fg=TEXTE,
                       activebackground=VERT, activeforeground=FOND,
                       borderwidth=0, font=("Segoe UI", self._e(11)))

        def retirer():
            if self._demander_oui_non(
                    "Supprimer la boucle",
                    f"Supprimer la boucle « {boucle['nom']} » ?",
                    oui="Supprimer", accent=ROUGE):
                boucles = self._boucles_courantes()
                if boucle in boucles:
                    boucles.remove(boucle)
                self._sauver_boucles()
                self._maj_boutons_boucles()

        menu.add_command(label="🗑  Retirer", command=retirer)
        try:
            menu.tk_popup(evenement.x_root, evenement.y_root)
        finally:
            menu.grab_release()

    def _maj_boutons_boucles(self):
        """Reconstruit les chips des boucles A-B enregistrées de la piste."""
        for widget in self.cadre_boucles.winfo_children():
            widget.destroy()
        if self.index_courant < 0:
            return
        boucles = self.boucles.get(self.playlist[self.index_courant], [])
        if not boucles:
            return
        police = tkfont.Font(family="Segoe UI", size=self._e(9))
        dispo = self.cadre_boucles.winfo_width()
        if dispo <= 1:
            dispo = self.racine.winfo_width() - 20
        ligne = tk.Frame(self.cadre_boucles, bg=FOND)
        ligne.pack(anchor="center")
        largeur_ligne = 0
        for bc in boucles:
            libelle = (f"🔁 {bc['nom']}  "
                       f"({formate_temps(int(bc['a']))} → "
                       f"{formate_temps(int(bc['b']))})")
            largeur_btn = police.measure(libelle) + self._e(30)
            if largeur_ligne > 0 and largeur_ligne + largeur_btn > dispo:
                ligne = tk.Frame(self.cadre_boucles, bg=FOND)
                ligne.pack(anchor="center")
                largeur_ligne = 0
            bouton = tk.Button(ligne, text=libelle,
                      command=lambda bc=bc: self._activer_boucle(bc),
                      bg=FOND_CLAIR, fg=VERT, activebackground=SURVOL_CLAIR,
                      activeforeground=VERT, relief="flat", borderwidth=0,
                      highlightthickness=0, font=("Segoe UI", self._e(9)),
                      padx=self._e(6), pady=self._e(2), takefocus=0)
            bouton.bind("<Button-3>", lambda e, bc=bc: self._supprimer_boucle(e, bc))
            bouton.bind("<space>", self._touche_espace)
            bouton.pack(side="left", padx=3, pady=2)
            largeur_ligne += largeur_btn

    def _dessiner_selection(self):
        """Surligne la zone sélectionnée (semi-transparente) sur la courbe."""
        c = self.canvas_onde
        c.delete("selection")
        duree = self._duree_courante()
        if self.sel_a is None or self.sel_b is None or duree <= 0:
            return
        a, b = sorted((self.sel_a, self.sel_b))
        fa, fb = a / duree, b / duree
        # Restreint au domaine visible (zoom)
        fa = max(fa, self.zoom_debut)
        fb = min(fb, self.zoom_fin)
        if fb <= fa:
            return
        x0 = self._fraction_au_pixel(fa)
        x1 = self._fraction_au_pixel(fb)
        c.create_rectangle(x0, 0, x1, c.winfo_height(), fill="#4a90d9",
                           stipple="gray50", width=0, tags="selection")

    # ------------------------------------------------------------------ #
    #  Édition audio (couper / fondus / rogner) — non destructif
    # ------------------------------------------------------------------ #
    def _editer(self, operation):
        """Applique une opération d'édition à la piste courante via ffmpeg.

        Non destructif : écrit un nouveau WAV à côté de la source, conserve
        l'original, remappe les repères et empile un instantané pour l'undo.
        """
        if self._edition_en_cours:
            return
        if self.index_courant < 0:
            self._info("Édition", "Sélectionne d'abord une piste.", accent=ROUGE)
            return
        duree = self._duree_courante()
        if duree <= 0:
            return
        besoin_selection = operation in ("couper", "rogner")
        a = b = None
        if self.sel_a is not None and self.sel_b is not None:
            a, b = sorted((self.sel_a, self.sel_b))
        if besoin_selection and (a is None or b - a < 1):
            self._info("Édition",
                       "Sélectionne d'abord une zone (Shift + glisser sur la "
                       "courbe).", accent=ROUGE)
            return
        chemin = self.playlist[self.index_courant]
        sortie = self._sortie_edition(chemin)
        filtre = self._filtre_edition(operation, a, b, duree)
        if filtre is None:
            return
        # Instantané AVANT modification (pour l'annulation)
        self._empiler_undo()
        self._edition_en_cours = True
        self.label_selection.config(text="Édition en cours…")
        threading.Thread(target=self._editer_thread,
                         args=(operation, chemin, sortie, filtre,
                               self.index_courant, a, b, duree),
                         daemon=True).start()

    def _filtre_edition(self, operation, a, b, duree):
        """Construit les arguments ffmpeg du filtre audio pour `operation`.

        Renvoie une liste d'arguments (après le -i), ou None si invalide.
        Les durées sont en secondes ; a/b en ms (sélection) éventuellement None.
        """
        D = duree / 1000.0
        if operation == "couper":
            A, B = a / 1000.0, b / 1000.0
            if A <= 0.0:                      # rien avant : reste = [B, fin]
                return ["-af", f"atrim=start={B:.3f},asetpts=PTS-STARTPTS"]
            if B >= D:                        # rien après : reste = [0, A]
                return ["-af", f"atrim=end={A:.3f}"]
            return ["-filter_complex",
                    f"[0:a]atrim=end={A:.3f}[h];"
                    f"[0:a]atrim=start={B:.3f},asetpts=PTS-STARTPTS[t];"
                    f"[h][t]concat=n=2:v=0:a=1[out]",
                    "-map", "[out]"]
        if operation == "rogner":
            A, B = a / 1000.0, b / 1000.0
            return ["-af",
                    f"atrim=start={A:.3f}:end={B:.3f},asetpts=PTS-STARTPTS"]
        if operation == "fade_in":
            if a is not None:
                st, d = a / 1000.0, (b - a) / 1000.0
            else:
                st, d = 0.0, min(3.0, D)      # défaut : 3 s en début
            return ["-af", f"afade=t=in:st={st:.3f}:d={max(0.01, d):.3f}"]
        if operation == "fade_out":
            if a is not None:
                st, d = a / 1000.0, (b - a) / 1000.0
            else:
                d = min(3.0, D)
                st = max(0.0, D - d)          # défaut : 3 s en fin
            return ["-af", f"afade=t=out:st={st:.3f}:d={max(0.01, d):.3f}"]
        return None

    def _editer_thread(self, operation, chemin, sortie, filtre, index, a, b, duree):
        ok = False
        try:
            subprocess.run(
                [self.ffmpeg, "-v", "quiet", "-y", "-i", chemin]
                + filtre + ["-c:a", "pcm_s16le", sortie],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=True)
            ok = os.path.exists(sortie)
        except Exception:
            ok = False
        _, nouvelle_duree = (self._infos_fichier(sortie) if ok else (None, 0))
        self.racine.after(0, lambda: self._fin_edition(
            ok, operation, chemin, sortie, index, a, b, duree, nouvelle_duree))

    def _fin_edition(self, ok, operation, chemin, sortie, index, a, b,
                     duree, nouvelle_duree):
        self._edition_en_cours = False
        if not ok:
            # Échec : on retire l'instantané inutile empilé avant l'opération.
            if self._historique:
                self._historique.pop()
            self._maj_boutons_undo()
            self._maj_label_selection()
            self._info("Édition", "L'opération a échoué.", accent=ROUGE)
            return
        # Repères remappés selon l'opération
        anciens = self.commentaires.get(chemin, [])
        nouveaux = self._remapper_reperes(operation, anciens, a, b, duree)
        self._charger_etat_piste(index, sortie, nouvelle_duree, nouveaux)
        self.sel_a = self.sel_b = None
        self._maj_label_selection()

    def _remapper_reperes(self, operation, reperes, a, b, duree):
        """Recalcule les temps des repères après une édition."""
        out = []
        for cm in reperes:
            t = cm["temps"]
            if operation == "couper":
                if t <= a:
                    nt = t
                elif t >= b:
                    nt = t - (b - a)
                else:
                    continue              # repère dans la zone coupée : supprimé
            elif operation == "rogner":
                if a <= t <= b:
                    nt = t - a
                else:
                    continue              # hors de la zone gardée : supprimé
            else:                          # fondus : durée inchangée
                nt = t
            out.append({"temps": int(nt), "texte": cm["texte"]})
        return out

    def _sortie_edition(self, chemin):
        """Chemin de sortie « _edit.wav » (incrémenté pour ne rien écraser)."""
        base = os.path.splitext(chemin)[0]
        candidat = base + "_edit.wav"
        n = 2
        while os.path.exists(candidat):
            candidat = f"{base}_edit{n}.wav"
            n += 1
        return candidat

    # ------------------------------------------------------------------ #
    #  Historique d'édition (undo / redo)
    # ------------------------------------------------------------------ #
    def _snapshot(self, index):
        """Capture l'état éditable d'une piste (fichier, durée, repères)."""
        chemin = self.playlist[index]
        duree = self.durees[index] if index < len(self.durees) else 0
        cues = [dict(cm) for cm in self.commentaires.get(chemin, [])]
        return {"index": index, "chemin": chemin, "duree": duree, "cues": cues}

    def _empiler_undo(self):
        """Empile l'état courant pour l'annulation et vide la pile redo."""
        self._historique.append(self._snapshot(self.index_courant))
        self._refait.clear()
        self._maj_boutons_undo()

    def annuler_edition(self):
        """Annule la dernière édition (Ctrl+Z)."""
        if self._edition_en_cours or not self._historique:
            return
        snap = self._historique.pop()
        self._refait.append(self._snapshot(snap["index"]))
        self._charger_etat_piste(snap["index"], snap["chemin"], snap["duree"],
                                 snap["cues"])
        self._maj_boutons_undo()

    def refaire_edition(self):
        """Rétablit l'édition annulée (Ctrl+Y)."""
        if self._edition_en_cours or not self._refait:
            return
        snap = self._refait.pop()
        self._historique.append(self._snapshot(snap["index"]))
        self._charger_etat_piste(snap["index"], snap["chemin"], snap["duree"],
                                 snap["cues"])
        self._maj_boutons_undo()

    def _maj_boutons_undo(self):
        """Active/désactive les boutons ↶ ↷ selon les piles."""
        self.bouton_undo.config(
            state="normal" if self._historique else "disabled")
        self.bouton_redo.config(
            state="normal" if self._refait else "disabled")

    def _charger_etat_piste(self, index, chemin, duree, cues):
        """Applique (fichier, durée, repères) à une piste et rafraîchit l'UI."""
        if not (0 <= index < len(self.playlist)):
            return
        ancien = self.playlist[index]
        self.playlist[index] = chemin
        if index < len(self.durees):
            self.durees[index] = duree
            self._maj_temps_total()
        # Repères : on rattache la liste au nouveau chemin
        if ancien in self.commentaires and ancien != chemin:
            self.commentaires.pop(ancien, None)
        self.commentaires[chemin] = [dict(cm) for cm in cues]
        self._sauver_commentaires()
        self.liste.delete(index)
        self.liste.insert(index, self._libelle_liste(chemin, duree))
        self._surligner_courant()
        if index == self.index_courant:
            self._recharger_piste_courante(chemin, duree)

    def _recharger_piste_courante(self, chemin, duree):
        """Recharge le média édité (à l'arrêt, position 0) et redessine tout."""
        self.player.stop()
        self.player.set_media(self.instance.media_new(chemin))
        self.horloge_reference = None
        self.duree_actuelle = duree
        self.position_demandee = None
        self.position_precedente = 0
        self._derniere_position = 0
        self.zoom_debut, self.zoom_fin = 0.0, 1.0
        self._annuler_alerte()
        self.label_titre.config(text=os.path.basename(chemin))
        self.bouton_play.config(text="▶")
        self.barre.set(0)
        self._maj_compteurs(0, duree)
        self._lancer_forme_onde(chemin)     # redécode la courbe du fichier édité
        self._maj_boutons_reperes()
        self._maj_boutons_boucles()

    def _maj_curseur_onde(self, position, duree):
        """Déplace le trait vertical de lecture sur la forme d'onde."""
        c = self.canvas_onde
        if self.forme_onde is None or duree <= 0:
            self._effacer_curseur()
            return
        self._derniere_position = position
        fraction = position / duree
        # Si on est zoomé et que le curseur sort de la fenêtre visible pendant
        # la lecture, on fait défiler la fenêtre (zoom conservé) pour que le
        # marqueur reste toujours visible. À l'arrêt, on ne touche à rien : on
        # respecte un zoom/déplacement manuel de l'utilisateur.
        span = self.zoom_fin - self.zoom_debut
        hors_champ = fraction < self.zoom_debut or fraction > self.zoom_fin
        if span < 1.0 and hors_champ and self.player.is_playing():
            marge = span * 0.1   # le curseur réapparaît un peu après le bord gauche
            debut = max(0.0, min(1.0 - span, fraction - marge))
            self.zoom_debut, self.zoom_fin = debut, debut + span
            self._dessiner_onde()   # redessine la courbe (et efface le curseur)
            hors_champ = False
        self._dessiner_partie_jouee()   # met à jour la teinte de progression
        if hors_champ:
            self._effacer_curseur()
            return   # position hors de la zone visible (zoom) : pas de curseur
        # Trait réutilisé d'une image à l'autre : on ne fait que le déplacer
        # (coords) au lieu de le supprimer/recréer, pour ne repeindre qu'une
        # fine bande au lieu de toute la zone du curseur à chaque frame.
        x = int(self._fraction_au_pixel(fraction))
        if self._id_curseur is not None and x == self._dernier_px_curseur:
            return   # même pixel qu'à la dernière image : trait déjà au bon endroit
        self._dernier_px_curseur = x
        h = c.winfo_height()
        if self._id_curseur is None:
            self._id_curseur = c.create_line(x, 0, x, h, fill="#ffffff",
                                              width=2, tags="curseur")
        else:
            c.coords(self._id_curseur, x, 0, x, h)

    def _effacer_curseur(self):
        """Retire le trait de lecture s'il existe (hors champ, pas de piste)."""
        if self._id_curseur is not None:
            self.canvas_onde.delete(self._id_curseur)
            self._id_curseur = None

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
        fraction = max(0.0, min(1.0, self._pixel_a_fraction(evenement.x)))
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
        self._maj_compteurs(position, duree)
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
        if self._demander_oui_non(
                "Cue", f"Créer un cue à {formate_temps(temps)} ?"):
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
        """Durée de la piste (ms) : valeur ffmpeg (fidèle) en priorité.

        `get_length()` de VLC est une estimation peu fiable sur les MP3 (VBR) :
        le curseur, calé sur position/durée, dérivait alors de plus en plus.
        La durée mesurée par ffmpeg (duree_actuelle) est exacte ; on l'utilise
        dès qu'elle est connue et on ne retombe sur VLC qu'en attendant.
        """
        return self.duree_actuelle if self.duree_actuelle > 0 \
            else self.player.get_length()

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
        hauteur = c.winfo_height()
        for cm in self.commentaires.get(self.playlist[self.index_courant], []):
            fraction = cm["temps"] / duree
            if fraction < self.zoom_debut or fraction > self.zoom_fin:
                continue   # repère hors de la zone visible (zoom)
            x = self._fraction_au_pixel(fraction)
            c.create_line(x, 0, x, hauteur, fill="#ffb300", width=2, tags="repere")
            c.create_polygon(x, 0, x + 12, 5, x, 11,
                             fill="#ffb300", outline="", tags="repere")

    def _repere_proche(self, x_clic):
        """Renvoie le repère dont le drapeau est à <8 px du clic, sinon None."""
        duree = self._duree_courante()
        if duree <= 0 or self.index_courant < 0:
            return None
        for cm in self.commentaires.get(self.playlist[self.index_courant], []):
            if abs(x_clic - self._fraction_au_pixel(cm["temps"] / duree)) <= 8:
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
            if self._demander_oui_non(
                    "Supprimer le repère",
                    f"Supprimer ce repère ?\n\n"
                    f"{formate_temps(existant['temps'])}  —  {existant['texte']}",
                    oui="Supprimer", accent=ROUGE):
                self._reperes_courants().remove(existant)
                self._sauver_commentaires()
                self._dessiner_onde()
                self._maj_boutons_reperes()
            return
        # Sinon -> nouveau repère au temps cliqué (selon le zoom)
        temps = int(max(0.0, min(1.0, self._pixel_a_fraction(evenement.x))) * duree)
        self._ajouter_repere(temps)

    def _ajouter_repere(self, temps):
        """Demande un texte et crée un repère (cue) au temps donné (ms)."""
        texte = self._demander_texte(
            "Nouveau cue", f"Commentaire à {formate_temps(temps)} :")
        if texte:
            reperes = self._reperes_courants()
            reperes.append({"temps": temps, "texte": texte})
            reperes.sort(key=lambda d: d["temps"])
            self._sauver_commentaires()
            self._dessiner_onde()
            self._maj_boutons_reperes()

    def _maj_boutons_reperes(self):
        """Reconstruit les boutons de repères, répartis sur plusieurs lignes."""
        for widget in self.cadre_reperes.winfo_children():
            widget.destroy()
        if self.index_courant < 0:
            return
        reperes = self.commentaires.get(self.playlist[self.index_courant], [])
        if not reperes:
            return
        police = tkfont.Font(family="Segoe UI", size=self._e(9))
        # Largeur disponible (sinon la largeur de la fenêtre avant 1er rendu)
        dispo = self.cadre_reperes.winfo_width()
        if dispo <= 1:
            dispo = self.racine.winfo_width() - 20
        ligne = tk.Frame(self.cadre_reperes, bg=FOND)
        ligne.pack(anchor="center")
        largeur_ligne = 0
        for cm in reperes:
            texte = cm["texte"]
            if len(texte) > 22:
                texte = texte[:21] + "…"
            libelle = f"{self._formate_total(cm['temps'])} ⟶ {texte}"
            largeur_btn = police.measure(libelle) + self._e(30)  # marges + écart
            # Nouvelle ligne si le bouton dépasse la largeur disponible
            if largeur_ligne > 0 and largeur_ligne + largeur_btn > dispo:
                ligne = tk.Frame(self.cadre_reperes, bg=FOND)
                ligne.pack(anchor="center")
                largeur_ligne = 0
            bouton = tk.Button(ligne, text=libelle,
                      command=lambda t=cm["temps"]: self._aller_repere(t),
                      bg=FOND_CLAIR, fg="#ffb300", activebackground=SURVOL_CLAIR,
                      activeforeground="#ffb300", relief="flat", borderwidth=0,
                      highlightthickness=0, font=("Segoe UI", self._e(9)),
                      padx=self._e(6), pady=self._e(2), takefocus=0)
            bouton.bind("<space>", self._touche_espace)
            bouton.pack(side="left", padx=3, pady=2)
            largeur_ligne += largeur_btn

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
                          font=("Segoe UI", self._e(9), "bold"), tags="infobulle")
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

    def _charger_boucles(self):
        try:
            with open(self._chemin_boucles(), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _sauver_boucles(self):
        try:
            with open(self._chemin_boucles(), "w", encoding="utf-8") as f:
                json.dump(self.boucles, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _chemin_boucles(self):
        """Fichier JSON des boucles A-B enregistrées, à côté du script (ou
        de l'exe)."""
        if getattr(sys, "frozen", False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, "boucles.json")

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
        if self.fenetre_compteurs is not None:
            self.fenetre_compteurs.config(bg=FOND)
            self.label_alerte_deporte.config(text="")

    def _declencher_decompte(self, commentaire):
        """Fenêtre principale : flash coloré semi-transparent + 3·2·1.

        Écran déporté (s'il est ouvert) : pas de flash — juste une fine
        bordure clignotante et un bandeau de texte, les compteurs restent
        entièrement visibles.
        """
        self._annuler_alerte()
        # Une couleur différente à chaque alerte (parcours de la palette)
        couleur = self._couleurs_alerte[
            self._index_couleur % len(self._couleurs_alerte)]
        self._index_couleur += 1
        self._couleur_alerte_courante = couleur
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
        if self.fenetre_compteurs is not None:
            self.label_alerte_deporte.config(fg=couleur)
        self._pulse_on = True
        self._pulser()
        self._etape_decompte(commentaire, 3)

    def _pulser(self):
        """Fait clignoter la transparence (principale) et la bordure (déportée)."""
        if not self._pulse_on:
            return
        self._pulse_etat = not self._pulse_etat
        self.overlay.attributes("-alpha", 0.55 if self._pulse_etat else 0.12)
        if self.fenetre_compteurs is not None:
            self.fenetre_compteurs.config(
                bg=self._couleur_alerte_courante if self._pulse_etat else FOND)
        self._apres_pulse = self.racine.after(280, self._pulser)

    def _etape_decompte(self, commentaire, n):
        if n > 0:
            # Décompte : commentaire annoncé + gros chiffre
            self.label_overlay_texte.config(text=f"« {commentaire['texte']} »")
            self.label_overlay_chiffre.config(text=str(n))
            if self.fenetre_compteurs is not None:
                self.label_alerte_deporte.config(
                    text=f"« {commentaire['texte']} »  —  {n}")
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

    def _maj_compteurs(self, position, duree):
        """Affiche écoulé et restant de façon cohérente (somme = durée).

        La position est arrondie une seule fois (entier de ms) et le restant en
        est déduit : les deux compteurs sont ainsi toujours complémentaires
        (leurs millisecondes totalisent exactement la durée), sans le décalage
        de 1 ms que produisaient deux troncatures indépendantes.
        """
        ecoule = int(max(0, min(position, duree)))
        self.label_ecoule.config(text=formate_temps(ecoule))
        self.label_restant.config(
            text=formate_temps(duree - ecoule, negatif=True))

    # ------------------------------------------------------------------ #
    #  Télécommande (réseau local) : serveur web + pont vers le thread Tk
    # ------------------------------------------------------------------ #
    @staticmethod
    def _ip_est_locale(ip):
        """Vrai si l'IP appartient à un réseau privé (réseau local)."""
        if ip.startswith(("192.168.", "10.", "127.", "169.254.")):
            return True
        if ip.startswith("172."):
            try:
                return 16 <= int(ip.split(".")[1]) <= 31
            except (IndexError, ValueError):
                return False
        return False

    def _ip_locale(self):
        """Adresse IP de la machine sur le réseau local (pour l'URL affichée)."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))   # n'envoie rien : donne l'interface sortante
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            s.close()

    def _demarrer_telecommande(self, port=8765):
        """Démarre le serveur web de télécommande (réseau local uniquement)."""
        self._commandes_remote = queue.Queue()
        self._etat_remote = {}
        self._maj_etat_remote()
        self._url_remote = None
        try:
            self._serveur_remote = ThreadingHTTPServer(
                ("0.0.0.0", port), _faire_handler_remote(self))
            self._serveur_remote.daemon_threads = True
            threading.Thread(target=self._serveur_remote.serve_forever,
                             daemon=True).start()
            self._url_remote = f"http://{self._ip_locale()}:{port}"
        except OSError:
            self._serveur_remote = None   # port occupé : télécommande indisponible
        # Pont : on traite les commandes reçues sur le thread principal de Tk.
        self._boucle_telecommande()

    def _afficher_qr(self, evenement=None):
        """Affiche un QR code de l'URL de la télécommande (à scanner au tél.)."""
        if not self._url_remote:
            return
        if qrcode is None:    # bibliothèque absente : on montre juste l'URL
            self._info("Télécommande", self._url_remote)
            return
        qr = qrcode.QRCode(border=2)
        qr.add_data(self._url_remote)
        qr.make(fit=True)
        matrice = qr.get_matrix()
        module = self._e(8)                 # taille d'un module (px)
        cote = len(matrice) * module
        top, cadre = self._creer_dialogue("Télécommande — scanne ce QR code", VERT)
        canvas = tk.Canvas(cadre, width=cote, height=cote, bg="#ffffff",
                           highlightthickness=0)
        canvas.pack(padx=self._e(24), pady=(0, self._e(12)))
        for y, ligne in enumerate(matrice):
            for x, plein in enumerate(ligne):
                if plein:
                    canvas.create_rectangle(x * module, y * module,
                                            (x + 1) * module, (y + 1) * module,
                                            fill="#000000", width=0)
        tk.Label(cadre, text=self._url_remote, bg=FOND_CLAIR, fg=TEXTE,
                 font=("Consolas", self._e(13))).pack(pady=(0, self._e(4)))
        tk.Label(cadre, text="Téléphone sur le même Wi‑Fi : scanne, ou tape "
                 "l'adresse dans le navigateur.", bg=FOND_CLAIR, fg="#9e9e9e",
                 font=("Segoe UI", self._e(10)), wraplength=cote).pack(
                 padx=self._e(24), pady=(0, self._e(10)))
        barre = tk.Frame(cadre, bg=FOND_CLAIR)
        barre.pack(fill="x", padx=self._e(24), pady=(0, self._e(20)))
        self._bouton_dialogue(barre, "Fermer", top.destroy,
                              primaire=True).pack(side="right")
        top.bind("<Escape>", lambda e: top.destroy())
        self._placer_dialogue(top)

    def _maj_etat_remote(self):
        """Construit l'instantané d'état exposé au téléphone (JSON)."""
        titre = "Aucune piste"
        if 0 <= self.index_courant < len(self.playlist):
            titre = os.path.basename(self.playlist[self.index_courant])
        self._etat_remote = {
            "titre": titre,
            "lecture": bool(self.player.is_playing()),
            "position": int(self._derniere_position),
            "duree": max(0, int(self._duree_courante())),
            "volume": int(float(self.volume.get())),
            "index": self.index_courant,
            "pistes": [os.path.basename(p) for p in self.playlist],
        }

    def _boucle_telecommande(self):
        """Exécute les commandes reçues du téléphone sur le thread Tk."""
        try:
            while True:
                cmd, val = self._commandes_remote.get_nowait()
                self._executer_commande_remote(cmd, val)
        except queue.Empty:
            pass
        self._maj_etat_remote()
        self.racine.after(200, self._boucle_telecommande)

    def _executer_commande_remote(self, cmd, val):
        """Applique une commande reçue (sur le thread principal)."""
        try:
            if cmd == "play_pause":
                self.play_pause()
            elif cmd == "stop":
                self.stop()
            elif cmd == "next":
                self.suivant()
            elif cmd == "prev":
                self.precedent()
            elif cmd == "volume" and val is not None:
                v = max(0, min(100, int(float(val))))
                self.volume.set(v)
                self.player.audio_set_volume(v)
            elif cmd == "lire" and val is not None:
                self._lire_index(int(val))
            elif cmd == "ajouter" and val:
                self._ajouter_chemin(val)
        except (ValueError, IndexError):
            pass

    def _ajouter_chemin(self, chemin):
        """Ajoute un seul fichier à la playlist (durée calculée en tâche de fond)."""
        if not chemin or not os.path.isfile(chemin):
            return False
        self.playlist.append(chemin)
        self.durees.append(0)
        index = len(self.playlist) - 1
        self.liste.insert("end", self._libelle_liste(chemin, 0))
        threading.Thread(target=self._charger_infos_liste,
                         args=([(index, chemin)],), daemon=True).start()
        return True

    def _dossier_recus(self):
        """Dossier où sont enregistrés les fichiers reçus du téléphone."""
        if getattr(sys, "frozen", False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        dossier = os.path.join(base, "Reçus téléphone")
        try:
            os.makedirs(dossier, exist_ok=True)
        except OSError:
            pass
        return dossier

    def _enregistrer_recu(self, nom, donnees):
        """Enregistre un fichier téléversé (nom unique). Renvoie le chemin ou None."""
        nom = os.path.basename(nom or "")     # sécurité : pas de chemin relatif
        if not nom or donnees is None:
            return None
        chemin = os.path.join(self._dossier_recus(), nom)
        base, ext = os.path.splitext(chemin)
        n = 2
        while os.path.exists(chemin):
            chemin = f"{base}_{n}{ext}"
            n += 1
        try:
            with open(chemin, "wb") as f:
                f.write(donnees)
            return chemin
        except OSError:
            return None

    def _parcourir_dossier(self, chemin):
        """Liste sous-dossiers + fichiers audio d'un répertoire (télécommande).

        Chemin vide = racine : lecteurs du PC + dossier Musique de l'utilisateur.
        """
        if not chemin:
            try:
                lecteurs = list(os.listdrives())          # Python 3.12+
            except AttributeError:
                lecteurs = [f"{c}:\\" for c in "CDEFGHIJKLMNOP"
                            if os.path.exists(f"{c}:\\")]
            dossiers = [{"nom": d, "chemin": d} for d in lecteurs]
            musique = os.path.join(os.path.expanduser("~"), "Music")
            if os.path.isdir(musique):
                dossiers.insert(0, {"nom": "Musique", "chemin": musique})
            return {"chemin": "", "parent": None,
                    "dossiers": dossiers, "fichiers": []}
        chemin = os.path.abspath(chemin)
        dossiers, fichiers = [], []
        try:
            for nom in sorted(os.listdir(chemin), key=str.lower):
                p = os.path.join(chemin, nom)
                if os.path.isdir(p):
                    dossiers.append({"nom": nom, "chemin": p})
                elif nom.lower().endswith(EXT_AUDIO):
                    fichiers.append({"nom": nom, "chemin": p})
        except OSError:
            pass
        parent = os.path.dirname(chemin)
        if parent == chemin:                # déjà à la racine d'un lecteur
            parent = ""
        return {"chemin": chemin, "parent": parent,
                "dossiers": dossiers, "fichiers": fichiers}

    # ------------------------------------------------------------------ #
    #  Rafraîchissement automatique
    # ------------------------------------------------------------------ #
    def _rafraichir(self):
        self._maj_surbrillance_carts()
        if self.fenetre_compteurs is not None:
            self.label_titre_deporte.config(text=self.label_titre.cget("text"))
            self.label_ecoule_deporte.config(text=self.label_ecoule.cget("text"))
            self.label_restant_deporte.config(text=self.label_restant.cget("text"))
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

            # Affichage calé sur la durée FIDÈLE (ffmpeg) : get_length() de VLC
            # surestime les MP3 (VBR) et faisait dériver le curseur. get_time()
            # avance en temps réel, donc position/duree_fidele reste aligné.
            duree_aff = self._duree_courante()
            if duree_aff > 0:
                position = min(position, duree_aff)  # ne pas dépasser la fin
                # Boucle A-B : dès qu'on atteint B, on revient au point A.
                if (self.boucle_ab.get() and self.sel_a is not None
                        and self.sel_b is not None):
                    a, b = sorted((self.sel_a, self.sel_b))
                    if b - a >= 1 and position >= b:
                        self.player.set_time(int(a))
                        self.dernier_temps_vlc = int(a)
                        self.temps_reference = a
                        self.horloge_reference = time.perf_counter()
                        position = a
                self.barre.set((position / duree_aff) * 1000)
                self._maj_compteurs(position, duree_aff)
                self._maj_curseur_onde(position, duree_aff)
                self._verifier_reperes(position)
        elif not self.player.is_playing():
            # En pause/arrêt : on coupe l'interpolation pour repartir propre.
            self.horloge_reference = None
            # On garde la référence à jour pour ne pas alerter en reprenant.
            temps = self.player.get_time()
            if temps >= 0:
                self.position_precedente = temps

        # Fin de morceau : on passe à la piste suivante, UNE seule fois.
        # L'état Ended persiste plusieurs frames : on ne réagit qu'à la
        # transition vers Ended, sinon on avancerait en boucle.
        #   - boucle cochée   -> on la lance (lecture continue en boucle)
        #   - boucle décochée -> on la charge sans la lancer (lecture arrêtée)
        etat = self.player.get_state()
        if (etat == vlc.State.Ended and self._dernier_etat != vlc.State.Ended
                and self.playlist):
            if self.index_courant + 1 < len(self.playlist):
                # Il reste une piste après : on y passe (lue si boucle activée)
                self._lire_index(self.index_courant + 1, lire=self.boucle.get())
            elif self.boucle.get():
                # Dernière piste + boucle : on recommence la playlist
                self._lire_index(0, lire=True)
            # else : dernière piste, boucle off -> on s'arrête (rien à charger)
        self._dernier_etat = etat

        self.racine.after(16, self._rafraichir)


if __name__ == "__main__":
    racine = tk.Tk()
    app = LecteurAudio(racine)
    racine.mainloop()
