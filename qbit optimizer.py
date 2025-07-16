import qbittorrentapi
import re
import time
import logging

# Configuration
QB_URL = 'http://127.0.0.1:8080'
QB_USERNAME = 'admin'
QB_PASSWORD = 'adminadmin'
POLL_INTERVAL = 30  # Seconds between checks

# Match patterns like S01E02, 1x02
TV_SERIES_NAME_PATTERN = re.compile(
    r'(?:S(?P<season>\d{1,2})E(?P<episode>\d{1,2}))|(?P<alt_season>\d{1,2})[xX](?P<alt_episode>\d{1,2})',
    re.IGNORECASE
)

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# Track prioritized episode per torrent hash
last_prioritized = {}

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
            season = match.group('season') or match.group('alt_season')
            episode = match.group('episode') or match.group('alt_episode')
            try:
                episodes.append((int(season), int(episode), file['index'], file['progress'], file['name']))
            except ValueError:
                continue
    episodes.sort(key=lambda x: (x[0], x[1]))
    return episodes

def update_file_priority(qb, torrent_hash, file_index, desired_priority):
    qb.torrents_file_priority(torrent_hash=torrent_hash, file_ids=[file_index], priority=desired_priority)

def manage_priorities():
    qb = connect_to_qb()
    global last_prioritized

    while True:
        torrents = qb.torrents_info()
        for torrent in torrents:
            if torrent.state.lower() == 'downloading':
                files = qb.torrents_files(torrent_hash=torrent.hash)
                sorted_episodes = get_sorted_episodes(files)

                if not sorted_episodes:
                    continue

                # Find first incomplete episode
                next_ep = None
                for ep in sorted_episodes:
                    if ep[3] < 1.0:  # progress < 1.0
                        next_ep = ep
                        break

                if not next_ep:
                    logging.info(f"All episodes completed for torrent: {torrent.name}")
                    continue

                next_index = next_ep[2]
                torrent_hash = torrent.hash
                last_index = last_prioritized.get(torrent_hash)

                # Check if we need to update
                if last_index != next_index:
                    logging.info(f"Promoting new episode: {next_ep[4]}")
                    last_prioritized[torrent_hash] = next_index

                    # Set the next one to high
                    update_file_priority(qb, torrent_hash, next_index, 7)

                    # Lower previous one
                    if last_index is not None:
                        update_file_priority(qb, torrent_hash, last_index, 1)

                # Handle all other files
                for file in files:
                    file_index = file['index']
                    is_completed = file['progress'] == 1.0
                    if file_index != next_index:
                        if is_completed and file['priority'] != 0:
                            logging.info(f"Lowering priority of completed file: {file['name']}")
                            update_file_priority(qb, torrent_hash, file_index, 0)
                        elif not is_completed and file['priority'] != 1:
                            update_file_priority(qb, torrent_hash, file_index, 1)
        time.sleep(POLL_INTERVAL)

if __name__ == '__main__':
    manage_priorities()