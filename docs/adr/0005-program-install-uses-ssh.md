# Installing a robot program uses SSH

The NAOqi session can push an autostart assignment, and it cannot place a release on disk or restart the program that runs it. The Microphone bridge and the Autostart process are installed by the release's own script: the server downloads that release, then copies it over SSH as `nao`. The password is typed for that install and is not stored. An accepted host key is remembered for that robot address. The assignment and its lease stay on the NAOqi session.
