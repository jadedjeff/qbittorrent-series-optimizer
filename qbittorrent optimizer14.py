import sys
import subprocess
import threading
import time
import logging
import re

# -------------------- Helper Functions --------------------
def check_pip_update():
    """Check if a newer version of pip is available and prompt the user to update."""
    try:
        import pip
        from packaging import version

        current_version = pip.__version__

        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "pip", "--dry-run"],
            capture_output=True, text=True
        )

        match = re.search(r'pip-(\d+\.\d+(?:\.\d+)?)', result.stdout)
        if match:
            latest_version_str = match.group(1)
            if version.parse(latest_version_str) > version.parse(current_version):
                update = input(f"A newer version of pip is available ({latest_version_str}). Update now? (y/n): ").strip().lower()
                if update == "y":
                    subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
    except Exception as e:
        print(f"Could not check pip version: {e}")

# -------------------- Module Check --------------------
def check_and_install_modules():
    required_modules = {
        'qbittorrentapi': 'qbittorrent-api',
        'psutil': 'psutil'
    }

    check_pip_update()

    for module, pip_name in required_modules.items():
        try:
            __import__(module)
            print(f"Module '{module}' is already installed.")
        except ImportError:
            print(f"Module '{module}' is missing.")
            install = input(f"Do you want to install '{module}' now? (y/n): ").strip().lower()
            if install == 'y':
                subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])
            else:
                print(f"'{module}' is required. Exiting script.")
                sys.exit(1)

# -------------------- Imports --------------------
check_and_install_modules()
import qbittorrentapi
import psutil

# -------------------- Configuration --------------------
QB_URL = 'http://127.0.0.1:8080'
QB_USERNAME = 'admin'
QB_PASSWORD = 'adminadmin'
POLL_INTERVAL = 2       # Seconds between checks
STALL_WAIT_TIME = 300   # Seconds before restarting a stalled torrent

# -------------------- Regex Pattern --------------------
TV_SERIES_NAME_PATTERN = re.compile(
    r"""(?ix)                                   # case-insensitive, verbose mode
    (?:                                         
        # Standard S01E01
        S(?P<season>\d{1,2})E(?P<episode>\d{1,3})
        |
        # 1x01 style
        (?P<alt_season>\d{1,2})[xX](?P<alt_episode>\d{1,3})
        |
        # Season 1 Episode 1, Season 1 Ep 1
        Season\s*(?P<long_season>\d{1,2})      
        (?:\s*Episode|\s*Ep\.?)\s*(?P<long_episode>\d{1,3})
        |
        # Anime-style: Ep 12 / Episode 12
        (?:Ep(?:isode)?\.?\s*)(?P<anime_episode>\d{1,3})
        |
        # Fallback: just a number (anime releases like [SubsPlease] Title - 12)
        [\s\-\_\.]\(?(?P<solo_episode>\d{1,3})(?!\d)
    )
    """,
    re.VERBOSE | re.IGNORECASE
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
last_prioritized = {}

# -------------------- Helpers --------------------
def wait_for_qbittorrent():
    logging.info("Waiting for qBittorrent to open...")
    while True:
        for proc in psutil.process_iter(['name']):
            if proc.info['name'] and "qbittorrent" in proc.info['name'].lower():
                logging.info("qBittorrent is running!")
                return
        time.sleep(2)

def connect_to_qb():
    qb = qbittorrentapi.Client(host=QB_URL, username=QB_USERNAME, password=QB_PASSWORD)
    try:
        qb.auth_log_in()
    except qbittorrentapi.LoginFailed as e:
        logging.error(f"Login failed: {e}")
        exit(1)
    return qb

def get_sorted_episodes(files):
    episodes = []
    for file in files:
        match = TV_SERIES_NAME_PATTERN.search(file['name'])
        if match:
            season = (
                match.group('season') or
                match.group('alt_season') or
                match.group('long_season')
            )
            episode = (
                match.group('episode') or
                match.group('alt_episode') or
                match.group('long_episode') or
                match.group('anime_episode') or
                match.group('solo_episode')
            )
            if episode:
                try:
                    season_num = int(season) if season else 1  # default to 1 for anime/no season
                    episode_num = int(episode)
                    episodes.append((season_num, episode_num, file['index'], file['progress'], file['name']))
                except ValueError:
                    continue
    episodes.sort(key=lambda x: (x[0], x[1]))
    return episodes

def update_file_priority(qb, torrent_hash, file_index, desired_priority):
    qb.torrents_file_priority(torrent_hash=torrent_hash, file_ids=[file_index], priority=desired_priority)

def any_torrents_active(qb):
    active_states = {"downloading", "stalledDL", "metaDL", "checkingDL", "allocating"}
    torrents = qb.torrents_info()
    for torrent in torrents:
        if torrent.state in active_states:
            return True
        files = qb.torrents_files(torrent.hash)
        if files and any(file['progress'] < 1.0 for file in files):
            return True
    return False

def remove_completed_torrent(qb, torrent):
    logging.info(f"Removing completed torrent (keeping files): {torrent.name}")
    qb.torrents_delete(delete_files=False, torrent_hashes=torrent.hash)

def timed_input(prompt, timeout=15):
    result = [None]
    def get_input():
        result[0] = input(prompt).strip().lower()
    thread = threading.Thread(target=get_input)
    thread.daemon = True
    thread.start()
    thread.join(timeout)
    return result[0]

def force_restart_torrent(qb, torrent):
    """Force restart a stalled torrent by pausing and resuming it."""
    try:
        logging.info(f"Forcing restart of stalled torrent: {torrent.name}")
        qb.torrents_pause(torrent.hash)
        time.sleep(2)
        qb.torrents_resume(torrent.hash)
    except Exception as e:
        logging.error(f"Failed to restart torrent {torrent.name}: {e}")

# -------------------- Main Logic --------------------
def manage_priorities():
    wait_for_qbittorrent()
    logging.info("==============================================")
    logging.info(" Welcome to qBittorrent Optimizer.")
    logging.info("==============================================")

    qb = connect_to_qb()

    # Wait up to 5 minutes if no torrents are active at start
    start_time = time.time()
    while not any_torrents_active(qb):
        if time.time() - start_time >= 300:  # 5 minutes
            logging.info("No active torrents found within 5 minutes. Exiting.")
            return
        logging.info("No active torrents. Waiting for activity...")
        time.sleep(10)

    global last_prioritized
    removed_torrents = set()
    stalled_since = {}

    while True:
        torrents = qb.torrents_info()
        for torrent in torrents:
            torrent_hash = torrent.hash
            if torrent.state == "metaDL":
                logging.info(f"Skipping {torrent.name} (metadata still downloading)")
                continue

            files = qb.torrents_files(torrent_hash=torrent_hash)
            sorted_episodes = get_sorted_episodes(files)

            # Completed file priority adjustment
            for file in files:
                if file['progress'] >= 1.0 and file['priority'] != 0:
                    logging.info(f"Marking completed file as 'Do Not Download': {file['name']}")
                    update_file_priority(qb, torrent_hash, file['index'], 0)

            # Remove fully completed torrents
            if files and all(file['progress'] >= 1.0 for file in files):
                if torrent_hash not in removed_torrents:
                    logging.info(f"All files completed. Removing torrent: {torrent.name} (keeping files)")
                    remove_completed_torrent(qb, torrent)
                    removed_torrents.add(torrent_hash)
                continue

            # Episode prioritization
            if sorted_episodes:
                next_ep = next((ep for ep in sorted_episodes if ep[3] < 1.0), None)
                if next_ep:
                    next_index = next_ep[2]
                    last_index = last_prioritized.get(torrent_hash)
                    if last_index != next_index:
                        logging.info(f"Promoting episode: {next_ep[4]}")
                        last_prioritized[torrent_hash] = next_index
                        update_file_priority(qb, torrent_hash, next_index, 7)
                        if last_index is not None:
                            update_file_priority(qb, torrent_hash, last_index, 1)

            # Handle stalled torrents
            if torrent.state == "stalledDL":
                if torrent_hash not in stalled_since:
                    stalled_since[torrent_hash] = time.time()
                    logging.info(f"{torrent.name} is stalled. Monitoring...")
                elif time.time() - stalled_since[torrent_hash] >= STALL_WAIT_TIME:
                    force_restart_torrent(qb, torrent)
                    stalled_since[torrent_hash] = time.time()  # reset after restart
            else:
                stalled_since.pop(torrent_hash, None)  # reset if torrent resumes

        # Shutdown check: exit only if no torrents left
        if not any_torrents_active(qb):
            logging.info("All downloads completed.")
            user_input = timed_input("Press 'n' within 15 seconds to continue monitoring, or anything else to exit: ", 15)
            if user_input != 'n':
                logging.info("Exiting script and closing qBittorrent...")
                try:
                    qb.app_shutdown()
                except Exception as e:
                    logging.warning(f"Could not shut down qBittorrent: {e}")
                break

        time.sleep(POLL_INTERVAL)

# -------------------- Entry Point --------------------
if __name__ == "__main__":
    manage_priorities()