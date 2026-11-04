# Known robots live on the server

## Status

Accepted

## Context

A fair laptop opens the NAO Fair app in more than one window. The address field used to remember one IP in that browser, and the robot's name disappeared on disconnect.

## Decision

A known robot is stored with the server's other data: the address that connected successfully, and the name that robot reported. The list is shared by every window, capped at 15, and newest first. The browser no longer stores an address. Clearing the field forgets nothing on the server. Removing a suggestion deletes that known robot.

## Considered Options

Keeping the list in the browser would have matched the old remembered address, and each window would have shown a different list.
