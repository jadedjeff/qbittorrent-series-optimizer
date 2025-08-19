import qbittorrentapi
import re
import time
import logging
import psutil   # for process detection

# -------------------- Configuration --------------------
QB_URL = 'http://127.0.0.1:8080'
QB_USERNAME = 'admin'
QB_PASSWORD = 'adminadmin'
POLL_INTERVAL = 5  # Seconds between checks

# Regex for episode format: S01E02 or 1x02
TV_SERIES_NAME_PATTERN = re.compile(
    r'(?:S(?P<season>\d{1,2})E(?P<episode>\d{1,2}))|(?P<alt_season>\d{1,2})[xX](?P<alt_episode>\d{1,2})',
    re.IGNORECASE
)

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
last_prioritized = {}

# -------------------- Helpers --------------------

def wait_for_qbittorrent():
    """Wait until qBittorrent is running."""
    logging.info("Waiting for qBittorrent to open...")
    while True:
        for proc in psutil.process_iter(['name']):
            if proc.info['name'] and proc.info['name'].lower() == "qbittorrent.exe":
                logging.info("qBittorrent is running!")
                return
        time.sleep(2)

def connect_to_qb():
    """Connect to the qBittorrent Web API."""
    qb = qbittorrentapi.Client(host=QB_URL, username=QB_USERNAME, password=QB_PASSWORD)
    try:
        qb.auth_log_in()
    except qbittorrentapi.LoginFailed as e:
        logging.error(f"Login failed: {e}")
        exit(1)
    return qb

def get_sorted_episodes(files):
    """Extract and sort episodes from torrent files."""
    episodes = []
    for file in files:
        match = TV_SERIES_NAME_PATTERN.search(file['name'])
        if match:
            season = match.group('season') or match.group('alt_season')
            episode = match.group('episode') or match.group('alt_episode')
            try:
                episodes.append((int(season), int(episode), file['index'], file['progress'], file['name']))
            except ValueError:
                continue
    episodes.sort(key=lambda x: (x[0], x[1]))
    return episodes

def update_file_priority(qb, torrent_hash, file_index, desired_priority):
    """Set file priority in a torrent."""
    qb.torrents_file_priority(torrent_hash=torrent_hash, file_ids=[file_index], priority=desired_priority)

def any_torrents_active(qb):
    """Return True if any torrent has incomplete files."""
    torrents = qb.torrents_info()
    for torrent in torrents:
        files = qb.torrents_files(torrent.hash)
        for file in files:
            if file['progress'] < 1.0:
                return True
    return False

def remove_completed_torrent(qb, torrent):
    """Remove a torrent from qBittorrent but keep the downloaded files."""
    logging.info(f"Removing completed torrent (keeping files): {torrent.name}")
    qb.torrents_delete(delete_files=False, torrent_hashes=torrent.hash)

# -------------------- Main Logic --------------------

def manage_priorities():
    # --- Wait until qBittorrent is open ---
    wait_for_qbittorrent()

    # --- Welcome message ---
    logging.info("==============================================")
    logging.info(" Welcome to qBittorrent Optimizer.")
    logging.info(" We will notify you of any events that take place.")
    logging.info("==============================================")

    qb = connect_to_qb()
    global last_prioritized
    removed_torrents = set()

    while True:
        torrents = qb.torrents_info()
        for torrent in torrents:
            torrent_hash = torrent.hash
            files = qb.torrents_files(torrent_hash=torrent_hash)
            sorted_episodes = get_sorted_episodes(files)

            # Mark completed files as "Do Not Download"
            for file in files:
                if file['progress'] >= 1.0 and file['priority'] != 0:
                    logging.info(f"Marking completed file as 'Do Not Download': {file['name']}")
                    update_file_priority(qb, torrent_hash, file['index'], 0)

            # Remove any torrent where all files are complete (keep downloaded files)
            if all(file['progress'] >= 1.0 for file in files):
                if torrent_hash not in removed_torrents:
                    logging.info(f"All files completed. Removing torrent: {torrent.name} (keeping files)")
                    remove_completed_torrent(qb, torrent)
                    removed_torrents.add(torrent_hash)
                continue  # Skip prioritization for fully completed torrents

            # Promote next episode (only applies to TV series)
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

        # Exit ONLY when all files in all torrents are fully downloaded
        if not any_torrents_active(qb):
            logging.info("All downloads completed.")
            user_input = input("Press 'y' and Enter to exit, or any other key to continue monitoring: ").strip().lower()
            if user_input == 'y':
                logging.info("Exiting script by user request...")
                break

        time.sleep(POLL_INTERVAL)

# -------------------- Entry Point --------------------

if __name__ == '__main__':
    manage_priorities()