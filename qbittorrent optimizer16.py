import sys
import subprocess
import threading
import time
import logging
import re
import psutil

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

# -------------------- Configuration --------------------
QB_URL = 'http://127.0.0.1:8080'
QB_USERNAME = 'admin'
QB_PASSWORD = 'adminadmin'
POLL_INTERVAL = 2       # Seconds between checks
STALL_WAIT_TIME = 300   # Seconds before restarting a stalled torrent
START_WAIT_TIME = 120   # Seconds (2-minute wait before exiting if no activity)

# -------------------- Regex Pattern --------------------
TV_SERIES_NAME_PATTERN = re.compile(
    r"""(?ix)                                 
    (?:                                         
        S(?P<season>\d{1,2})E(?P<episode>\d{1,3})          
        |                                       
        (?P<alt_season>\d{1,2})[xX](?P<alt_episode>\d{1,3})   
        |                                       
        Season\s*(?P<long_season>\d{1,2})      
        (?:\s*Episode|\s*Ep\.?)\s*(?P<long_episode>\d{1,3})
        |                                       
        (?:Ep(?:isode)?\.?\s*)(?P<anime_episode>\d{1,3})   
        |                                       
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
                    season_num = int(season) if season else 1
                    episode_num = int(episode)
                    episodes.append((season_num, episode_num, file['index'], file['progress'], file['name']))
                except ValueError:
                    continue
    episodes.sort(key=lambda x: (x[0], x[1]))
    return episodes


def update_file_priority(qb, torrent_hash, file_index, desired_priority):
    qb.torrents_file_priority(torrent_hash=torrent_hash, file_ids=[file_index], priority=desired_priority)


def mark_and_remove_seeding(qb, torrents):
    logging.info("All torrents are seeding. Marking files as 'Do Not Download' and removing torrents (files kept).")
    for torrent in torrents:
        try:
            files = qb.torrents_files(torrent.hash)
            for f in files:
                qb.torrents_file_priority(torrent.hash, [f.id], [0])
            qb.torrents_delete(delete_files=False, torrent_hashes=torrent.hash)
            logging.info(f"Closed torrent {torrent.name} (files kept).")
        except Exception as e:
            logging.error(f"Error handling torrent {torrent.name}: {e}")


def any_torrents_active(qb):
    active_states = {"downloading", "stalledDL", "metaDL", "checkingDL", "allocating"}
    seeding_states = {"uploading", "stalledUP", "queuedUP", "pausedUP"}

    torrents = qb.torrents_info()
    if not torrents:
        return False

    if all(t.state in seeding_states for t in torrents):
        mark_and_remove_seeding(qb, torrents)
        return False

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


def force_restart_torrent(qb, torrent):
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

    # -------------------- 2-minute countdown for no active torrents --------------------
    start_time = time.time()
    logging.info(f"No active torrents detected. The program will exit in {START_WAIT_TIME//60}:{START_WAIT_TIME%60:02d} minutes if no activity occurs.")

    while True:
        if any_torrents_active(qb):
            logging.info("Torrent activity detected. Proceeding...")
            break

        elapsed = time.time() - start_time
        remaining = int(START_WAIT_TIME - elapsed)
        if remaining <= 0:
            logging.info("No active torrents during 2-minute wait. Closing qBittorrent and exiting.")

            # Attempt to close qBittorrent gracefully
            try:
                qb.app_shutdown()
                logging.info("qBittorrent shutdown command sent successfully.")
            except Exception as e:
                logging.error(f"Failed to shutdown qBittorrent via API: {e}")

            # Forcefully terminate process if still running
            for proc in psutil.process_iter(['name']):
                if proc.info['name'] and "qbittorrent" in proc.info['name'].lower():
                    try:
                        proc.terminate()
                        logging.info(f"Terminated process: {proc.info['name']}")
                    except Exception as e:
                        logging.error(f"Failed to terminate process {proc.info['name']}: {e}")

            return

        mins, secs = divmod(remaining, 60)
        print(f"⏳ Exiting in {mins:02}:{secs:02} if no activity occurs...", end="\r")
        time.sleep(1)

    # -------------------- Main torrent management loop --------------------
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

            for file in files:
                if file['progress'] >= 1.0 and file['priority'] != 0:
                    logging.info(f"Marking completed file as 'Do Not Download': {file['name']}")
                    update_file_priority(qb, torrent_hash, file['index'], 0)

            if files and all(file['progress'] >= 1.0 for file in files):
                if torrent_hash not in removed_torrents:
                    logging.info(f"All files completed. Removing torrent: {torrent.name} (keeping files)")
                    remove_completed_torrent(qb, torrent)
                    removed_torrents.add(torrent_hash)
                continue

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

            if torrent.state == "stalledDL":
                if torrent_hash not in stalled_since:
                    stalled_since[torrent_hash] = time.time()
                    logging.info(f"{torrent.name} is stalled. Monitoring...")
                elif time.time() - stalled_since[torrent_hash] >= STALL_WAIT_TIME:
                    force_restart_torrent(qb, torrent)
                    stalled_since[torrent_hash] = time.time()
            else:
                stalled_since.pop(torrent_hash, None)

        if not any_torrents_active(qb):
            logging.info("All downloads complete or all torrents seeding. Exiting.")
            break

        time.sleep(POLL_INTERVAL)


# -------------------- Entry Point --------------------
if __name__ == "__main__":
    manage_priorities()
