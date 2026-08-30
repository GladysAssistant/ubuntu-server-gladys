#!/usr/bin/env python3
"""Small, dependency-free translation catalogue for the first-boot interface.

The installer runs before optional Python packages are present, so this module
deliberately keeps its catalogue in Python and treats /etc/default/locale as
data, never as a shell fragment.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

from i18n_catalog_east import ROWS as EASTERN_EUROPEAN_ROWS
from i18n_catalog_global import ROWS as GLOBAL_ROWS
from i18n_catalog_latin import ROWS as LATIN_REGIONAL_ROWS


_LANGUAGES = (
    "ar", "ast", "be", "bo", "ca", "cs", "de", "el", "en", "es", "fi",
    "fr", "gl", "he", "hr", "hu", "id", "ja", "kab", "lt", "lv", "nb",
    "nl", "oc", "pl", "pt", "ru", "sr", "sv", "uk", "zh_CN", "zh_TW",
)
_LANGUAGE_SET = frozenset(_LANGUAGES)
_UNRENDERABLE_CONSOLE_LANGUAGES = frozenset(
    {"ar", "bo", "he", "ja", "zh_CN", "zh_TW"}
)
DEFAULT_LOCALE_PATH = Path("/etc/default/locale")
_LOCALE_RE = re.compile(
    r"^(?P<language>[A-Za-z]{2,3})(?:_(?P<territory>[A-Za-z]{2}))?"
    r"(?:\.[A-Za-z0-9_-]+)?(?:@[A-Za-z0-9_-]+)?$"
)
_LANG_LINE_RE = re.compile(r"^\s*LANG\s*=\s*(?P<value>[^\r\n#]*)\s*$")


_UI_KEYS = (
    "brand", "preparing_title", "progress", "live", "connection_lost",
    "keep_connected", "download_note", "setup_address", "ready_title",
    "open_gladys", "fallback_address", "ethernet", "connected", "disconnected",
    "gladys", "running", "not_running", "fatal_title", "fatal_hint",
    "network_title", "network_body", "waiting", "step_system", "step_network",
    "step_packages", "step_docker", "step_download", "step_services",
    "status_complete", "status_active", "status_pending",
)
_PHASE_KEYS = (
    "PREFLIGHT", "WAIT_NETWORK", "APT_UPDATE", "INSTALL_PACKAGES",
    "START_SYSTEM_SERVICES", "DOCKER_PREFLIGHT", "PULL_GLADYS",
    "START_GLADYS", "VERIFY_GLADYS", "START_WATCHTOWER", "FINALIZE", "READY",
    "FATAL",
)


# Each row is intentionally complete.  Keeping the short appliance-oriented
# strings together makes it straightforward to audit additions to the UI.
_EN_UI = (
    "Gladys Assistant", "Preparing your connected home", "Progress", "Live",
    "Connection lost", "Keep this page open while setup finishes.",
    "Downloads may take a few minutes.", "Setup address", "Gladys Assistant is ready",
    "Open Gladys", "If the local name does not open, use this address.", "Ethernet",
    "connected", "disconnected", "Gladys", "running", "not running",
    "Setup cannot continue", "Run gladys-diagnostics for details.",
    "Internet connection required", "Connect this device to your router with an Ethernet cable.",
    "Waiting for connection…", "System", "Internet", "Packages", "Docker",
    "Download Gladys Assistant", "Start services", "Complete", "In progress", "Waiting",
)
_EN_PHASES = (
    "Checking system configuration…", "Waiting for an Internet connection…",
    "Updating package lists…", "Installing required packages…", "Starting system services…",
    "Checking Docker and Compose…", "Downloading Gladys Assistant…", "Starting Gladys Assistant…",
    "Checking that Gladys Assistant is ready…", "Starting automatic updates…",
    "Finishing setup…", "Gladys Assistant is ready", "Setup cannot continue",
)


# The order of strings in these rows is _UI_KEYS then _PHASE_KEYS.  All wording
# is deliberately short because it is also displayed on a small attached screen.
_ROWS: dict[str, tuple[str, ...]] = {
    "en": _EN_UI + _EN_PHASES,
    "fr": (
        "Gladys Assistant", "Préparation de votre maison connectée", "Progression", "En direct", "Connexion perdue", "Gardez cette page ouverte pendant la configuration.", "Les téléchargements peuvent prendre quelques minutes.", "Adresse de configuration", "Gladys Assistant est prêt", "Ouvrir Gladys", "Si le nom local ne s’ouvre pas, utilisez cette adresse.", "Ethernet", "connecté", "déconnecté", "Gladys", "en cours", "arrêté", "La configuration ne peut pas continuer", "Exécutez gladys-diagnostics pour les détails.", "Connexion Internet requise", "Connectez cet appareil à votre routeur avec un câble Ethernet.", "En attente de connexion…", "Système", "Internet", "Paquets", "Docker", "Télécharger Gladys Assistant", "Démarrer les services", "Terminé", "En cours", "En attente",
        "Vérification de la configuration système…", "En attente d’une connexion Internet…", "Mise à jour des listes de paquets…", "Installation des paquets requis…", "Démarrage des services système…", "Vérification de Docker et Compose…", "Téléchargement de Gladys Assistant…", "Démarrage de Gladys Assistant…", "Vérification de Gladys Assistant…", "Démarrage des mises à jour automatiques…", "Finalisation de la configuration…", "Gladys Assistant est prêt", "La configuration ne peut pas continuer",
    ),
    "de": (
        "Gladys Assistant", "Ihr vernetztes Zuhause wird vorbereitet", "Fortschritt", "Live", "Verbindung verloren", "Lassen Sie diese Seite geöffnet, bis die Einrichtung fertig ist.", "Downloads können einige Minuten dauern.", "Einrichtungsadresse", "Gladys Assistant ist bereit", "Gladys öffnen", "Falls der lokale Name nicht öffnet, verwenden Sie diese Adresse.", "Ethernet", "verbunden", "getrennt", "Gladys", "läuft", "läuft nicht", "Einrichtung kann nicht fortgesetzt werden", "Führen Sie gladys-diagnostics für Details aus.", "Internetverbindung erforderlich", "Verbinden Sie dieses Gerät per Ethernet-Kabel mit Ihrem Router.", "Warten auf Verbindung…", "System", "Internet", "Pakete", "Docker", "Gladys Assistant herunterladen", "Dienste starten", "Abgeschlossen", "Wird ausgeführt", "Warten",
        "Systemkonfiguration wird geprüft…", "Warten auf eine Internetverbindung…", "Paketlisten werden aktualisiert…", "Erforderliche Pakete werden installiert…", "Systemdienste werden gestartet…", "Docker und Compose werden geprüft…", "Gladys Assistant wird heruntergeladen…", "Gladys Assistant wird gestartet…", "Gladys Assistant wird geprüft…", "Automatische Updates werden gestartet…", "Einrichtung wird abgeschlossen…", "Gladys Assistant ist bereit", "Einrichtung kann nicht fortgesetzt werden",
    ),
    "es": (
        "Gladys Assistant", "Preparando tu hogar conectado", "Progreso", "En directo", "Conexión perdida", "Mantén esta página abierta mientras termina la configuración.", "Las descargas pueden tardar unos minutos.", "Dirección de configuración", "Gladys Assistant está listo", "Abrir Gladys", "Si el nombre local no abre, usa esta dirección.", "Ethernet", "conectado", "desconectado", "Gladys", "en ejecución", "no se está ejecutando", "La configuración no puede continuar", "Ejecuta gladys-diagnostics para ver los detalles.", "Se requiere conexión a Internet", "Conecta este dispositivo al router con un cable Ethernet.", "Esperando conexión…", "Sistema", "Internet", "Paquetes", "Docker", "Descargar Gladys Assistant", "Iniciar servicios", "Completado", "En curso", "En espera",
        "Comprobando la configuración del sistema…", "Esperando una conexión a Internet…", "Actualizando listas de paquetes…", "Instalando los paquetes necesarios…", "Iniciando servicios del sistema…", "Comprobando Docker y Compose…", "Descargando Gladys Assistant…", "Iniciando Gladys Assistant…", "Comprobando Gladys Assistant…", "Iniciando actualizaciones automáticas…", "Finalizando la configuración…", "Gladys Assistant está listo", "La configuración no puede continuar",
    ),
    "pt": (
        "Gladys Assistant", "A preparar a sua casa conectada", "Progresso", "Em direto", "Ligação perdida", "Mantenha esta página aberta enquanto a configuração termina.", "As transferências podem demorar alguns minutos.", "Endereço de configuração", "Gladys Assistant está pronto", "Abrir Gladys", "Se o nome local não abrir, use este endereço.", "Ethernet", "ligado", "desligado", "Gladys", "em execução", "não está em execução", "A configuração não pode continuar", "Execute gladys-diagnostics para obter detalhes.", "É necessária ligação à Internet", "Ligue este dispositivo ao router com um cabo Ethernet.", "A aguardar ligação…", "Sistema", "Internet", "Pacotes", "Docker", "Transferir Gladys Assistant", "Iniciar serviços", "Concluído", "Em curso", "A aguardar",
        "A verificar a configuração do sistema…", "A aguardar ligação à Internet…", "A atualizar listas de pacotes…", "A instalar os pacotes necessários…", "A iniciar serviços do sistema…", "A verificar Docker e Compose…", "A transferir Gladys Assistant…", "A iniciar Gladys Assistant…", "A verificar Gladys Assistant…", "A iniciar atualizações automáticas…", "A concluir a configuração…", "Gladys Assistant está pronto", "A configuração não pode continuar",
    ),
}


# Languages with close terminology use this carefully translated compact row
# factory.  It provides a complete, independent catalogue rather than falling
# back to English for any visible message.
def _row(
    *, home: str, progress: str, live: str, lost: str, keep: str, note: str,
    address: str, ready: str, open_: str, fallback: str, ethernet: str,
    connected: str, disconnected: str, running: str, not_running: str,
    fatal: str, hint: str, network: str, network_body: str, waiting: str,
    system: str, internet: str, packages: str, docker: str, download: str,
    services: str, complete: str, active: str, pending: str, phases: tuple[str, ...],
) -> tuple[str, ...]:
    return (
        "Gladys Assistant", home, progress, live, lost, keep, note, address, ready,
        open_, fallback, ethernet, connected, disconnected, "Gladys", running,
        not_running, fatal, hint, network, network_body, waiting, system, internet,
        packages, docker, download, services, complete, active, pending, *phases,
    )


def _phases(*values: str) -> tuple[str, ...]:
    if len(values) != len(_PHASE_KEYS):
        raise ValueError("a translation must provide every installer phase")
    return values


_ROWS.update({
    "nl": _row(home="Je verbonden huis voorbereiden", progress="Voortgang", live="Live", lost="Verbinding verbroken", keep="Houd deze pagina open terwijl de installatie wordt voltooid.", note="Downloads kunnen enkele minuten duren.", address="Installatieadres", ready="Gladys Assistant is klaar", open_="Gladys openen", fallback="Gebruik dit adres als de lokale naam niet opent.", ethernet="Ethernet", connected="verbonden", disconnected="niet verbonden", running="actief", not_running="niet actief", fatal="Installatie kan niet doorgaan", hint="Voer gladys-diagnostics uit voor details.", network="Internetverbinding vereist", network_body="Sluit dit apparaat met een ethernetkabel aan op uw router.", waiting="Wachten op verbinding…", system="Systeem", internet="Internet", packages="Pakketten", docker="Docker", download="Gladys Assistant downloaden", services="Diensten starten", complete="Voltooid", active="Bezig", pending="Wachten", phases=_phases("Systeemconfiguratie controleren…", "Wachten op een internetverbinding…", "Pakketlijsten bijwerken…", "Vereiste pakketten installeren…", "Systeemdiensten starten…", "Docker en Compose controleren…", "Gladys Assistant downloaden…", "Gladys Assistant starten…", "Gladys Assistant controleren…", "Automatische updates starten…", "Installatie afronden…", "Gladys Assistant is klaar", "Installatie kan niet doorgaan")),
    "sv": _row(home="Förbereder ditt uppkopplade hem", progress="Förlopp", live="Direkt", lost="Anslutningen bröts", keep="Låt sidan vara öppen medan installationen slutförs.", note="Hämtningar kan ta några minuter.", address="Installationsadress", ready="Gladys Assistant är klar", open_="Öppna Gladys", fallback="Använd den här adressen om det lokala namnet inte öppnas.", ethernet="Ethernet", connected="ansluten", disconnected="frånkopplad", running="körs", not_running="körs inte", fatal="Installationen kan inte fortsätta", hint="Kör gladys-diagnostics för mer information.", network="Internetanslutning krävs", network_body="Anslut enheten till routern med en Ethernet-kabel.", waiting="Väntar på anslutning…", system="System", internet="Internet", packages="Paket", docker="Docker", download="Hämta Gladys Assistant", services="Starta tjänster", complete="Klar", active="Pågår", pending="Väntar", phases=_phases("Kontrollerar systemkonfiguration…", "Väntar på internetanslutning…", "Uppdaterar paketlistor…", "Installerar nödvändiga paket…", "Startar systemtjänster…", "Kontrollerar Docker och Compose…", "Hämtar Gladys Assistant…", "Startar Gladys Assistant…", "Kontrollerar Gladys Assistant…", "Startar automatiska uppdateringar…", "Slutför installationen…", "Gladys Assistant är klar", "Installationen kan inte fortsätta")),
    "nb": _row(home="Forbereder ditt smarte hjem", progress="Fremdrift", live="Direkte", lost="Tilkoblingen ble brutt", keep="La denne siden være åpen mens oppsettet fullføres.", note="Nedlastinger kan ta noen minutter.", address="Oppsettsadresse", ready="Gladys Assistant er klar", open_="Åpne Gladys", fallback="Bruk denne adressen hvis det lokale navnet ikke åpnes.", ethernet="Ethernet", connected="tilkoblet", disconnected="frakoblet", running="kjører", not_running="kjører ikke", fatal="Oppsettet kan ikke fortsette", hint="Kjør gladys-diagnostics for detaljer.", network="Internettforbindelse kreves", network_body="Koble enheten til ruteren med en Ethernet-kabel.", waiting="Venter på tilkobling…", system="System", internet="Internett", packages="Pakker", docker="Docker", download="Last ned Gladys Assistant", services="Start tjenester", complete="Fullført", active="Pågår", pending="Venter", phases=_phases("Kontrollerer systemoppsett…", "Venter på Internettforbindelse…", "Oppdaterer pakkelister…", "Installerer nødvendige pakker…", "Starter systemtjenester…", "Kontrollerer Docker og Compose…", "Laster ned Gladys Assistant…", "Starter Gladys Assistant…", "Kontrollerer Gladys Assistant…", "Starter automatiske oppdateringer…", "Fullfører oppsettet…", "Gladys Assistant er klar", "Oppsettet kan ikke fortsette")),
    "fi": _row(home="Valmistellaan yhdistettyä kotiasi", progress="Edistyminen", live="Suorana", lost="Yhteys katkesi", keep="Pidä tämä sivu auki, kunnes asennus valmistuu.", note="Lataukset voivat kestää muutaman minuutin.", address="Asennusosoite", ready="Gladys Assistant on valmis", open_="Avaa Gladys", fallback="Käytä tätä osoitetta, jos paikallinen nimi ei avaudu.", ethernet="Ethernet", connected="yhdistetty", disconnected="ei yhdistetty", running="käynnissä", not_running="ei käynnissä", fatal="Asennusta ei voi jatkaa", hint="Saat lisätietoja komennolla gladys-diagnostics.", network="Internet-yhteys vaaditaan", network_body="Liitä tämä laite reitittimeen Ethernet-kaapelilla.", waiting="Odotetaan yhteyttä…", system="Järjestelmä", internet="Internet", packages="Paketit", docker="Docker", download="Lataa Gladys Assistant", services="Käynnistä palvelut", complete="Valmis", active="Käynnissä", pending="Odottaa", phases=_phases("Tarkistetaan järjestelmäasetuksia…", "Odotetaan Internet-yhteyttä…", "Päivitetään pakettiluetteloita…", "Asennetaan tarvittavia paketteja…", "Käynnistetään järjestelmäpalveluja…", "Tarkistetaan Docker ja Compose…", "Ladataan Gladys Assistant…", "Käynnistetään Gladys Assistant…", "Tarkistetaan Gladys Assistant…", "Käynnistetään automaattiset päivitykset…", "Viimeistellään asennusta…", "Gladys Assistant on valmis", "Asennusta ei voi jatkaa")),
})

_ROWS.update(EASTERN_EUROPEAN_ROWS)
_ROWS.update(GLOBAL_ROWS)
_ROWS.update(LATIN_REGIONAL_ROWS)


def _make_catalog(row: tuple[str, ...]) -> dict[str, dict[str, str]]:
    expected = len(_UI_KEYS) + len(_PHASE_KEYS)
    if len(row) != expected:
        raise RuntimeError("incomplete installer translation catalogue")
    return {
        "ui": dict(zip(_UI_KEYS, row[: len(_UI_KEYS)], strict=True)),
        "phases": dict(zip(_PHASE_KEYS, row[len(_UI_KEYS) :], strict=True)),
    }


_CATALOGS = {language: _make_catalog(_ROWS[language]) for language in _LANGUAGES}


def _normalize(language: object) -> str:
    """Return a whitelisted catalogue code, with English as the safe default."""
    if not isinstance(language, str):
        return "en"
    match = _LOCALE_RE.fullmatch(language.strip())
    if not match:
        return "en"
    base = match.group("language").lower()
    territory = match.group("territory")
    candidate = f"{base}_{territory.upper()}" if territory else base
    if candidate in _LANGUAGE_SET:
        return candidate
    return base if base in _LANGUAGE_SET else "en"


def read_installed_language(path: Path = DEFAULT_LOCALE_PATH) -> str:
    """Read the single LANG assignment in *path* without sourcing the file."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return "en"
    for line in lines:
        match = _LANG_LINE_RE.fullmatch(line)
        if not match:
            continue
        value = match.group("value").strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        elif any(character in value for character in "'\"\\`$;|&<>()"):
            return "en"
        return _normalize(value)
    return "en"


def supported_languages() -> tuple[str, ...]:
    """Return every locale exposed by the installer image."""
    return _LANGUAGES


def console_language(language: str) -> str:
    """Choose readable tty1 copy for the scripts Linux vt cannot render.

    The browser retains the full catalogue.  Linux's kernel virtual console
    provides neither Arabic shaping/bidirectional layout nor CJK/Tibetan font
    coverage, so those scripts use the English safety fallback on tty1.
    """
    selected = _normalize(language)
    return "en" if selected in _UNRENDERABLE_CONSOLE_LANGUAGES else selected


def catalog(language: str) -> Mapping[str, Mapping[str, str]]:
    """Return a fresh catalogue so callers cannot mutate shared translations."""
    selected = _CATALOGS[_normalize(language)]
    return {section: dict(values) for section, values in selected.items()}


def text(language: str, key: str) -> str:
    """Look up UI text, falling back to English for unknown keys/locales."""
    selected = _CATALOGS[_normalize(language)]["ui"]
    return selected.get(key, _CATALOGS["en"]["ui"].get(key, key))


def phase_text(language: str, phase: str) -> str:
    """Look up the human-readable message for an installer phase."""
    selected = _CATALOGS[_normalize(language)]["phases"]
    return selected.get(phase, _CATALOGS["en"]["phases"].get(phase, phase))


def direction(language: str) -> str:
    """Return the writing direction required by the selected locale."""
    return "rtl" if _normalize(language) in {"ar", "he"} else "ltr"
