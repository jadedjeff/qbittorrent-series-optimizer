This script version 6+ can used before or after you open qbittorent and you have an active torrent.
I have found this speeds up the overall download of a TV series

The newest version (5) will still automatically identify and prioritize TV series from the first episode to the last by changing the priority maximum for each and when a file completes it will mark it as "not downloaded" so that it will no longer be shared.
It will also now do mark any torrent that finishs (the files within the torrent) as "not downloaded" so that it will stop sharing them as well

I have added a lot if you liked this before it's much improved.

 You will need to install the qbittorrent api to get this to work here is how

Installing the qBittorrent API primarily involves two steps: enabling the Web UI in qBittorrent
and then installing the Python client library for interacting with it.
Enable qBittorrent Web UI:
Launch qBittorrent.
Navigate to Tools > Options (or Preferences on macOS/Linux).
Select the Web UI tab.
Check the box to Enable Web UI.
Configure the Listen port (default is 8080) and optionally set a Username and Password for authentication.
default Username:admin
default Password:adminadmin
******YOU CAN CHANGE THE DEFAULT USERNAME AND PASSWORD BUT NEED TO UPDATE IT IN THE .PY FILE AS WELL******
Click Apply or OK to save the changes.

This script will also remove torrents once they have finished downloading without removing the files
It will also shut down Qbittorrent and itself if there is no user input after all torrents are completed and removed (not the files)

you need python installed you can download at https://www.python.org/downloads/
(if on windows use the microsoft store) then use the following to install the neccesary dependencies 

Install the qbittorrent-api Python library:
Open your command prompt or terminal.
pip to install the library

        pip install qbittorrent-api
This command installs the necessary dependencies, including urllib3, requests, and attrdict
Install the psutil python library:
pip install psutil


and obviously you need python installed you can download at https://www.python.org/downloads/
