---
title: Using Steam with a screen reader on Windows, macOS, Linux, and SteamOS
lastModified: 2026-09-08
---

On Windows, macOS, and Linux, [Valve recommends Big Picture Mode](https://store.steampowered.com/news/app/593110/view/824835185591718585) for Steam's screen reader and keyboard support.

Start with the setup for your operating system, then continue to the Big Picture instructions:

- Windows: NVDA or JAWS.
- macOS: VoiceOver, with Steam launched through the Accessible Steam shortcut or the Terminal command below.
- Linux: Orca, started before Steam.
- Steam Deck and other SteamOS devices: go directly to the SteamOS section for Steam's built-in screen reader.

These instructions assume Steam is installed and you can sign in to your account. They cover using the client and checking whether a game is accessible. Games need their own accessibility support, even when Steam itself works with your screen reader. You can find accessibility notes and required mods in [our PC games database](https://blindgaming.net/pc).

Use the [Steam website](https://steampowered.com/) for purchases, reviews, account settings, and support. The client handles game installation and launching, friends, your library, achievements, and the What's New feed for updates and announcements.

## Windows

Start NVDA or JAWS, open Steam normally, then follow the Big Picture instructions below.

If the arrow keys read through the interface like a web page instead of moving Steam's selection, change how your screen reader handles them:

- With NVDA, press NVDA+Space to switch to focus mode. NVDA is your configured modifier key, usually Insert or Caps Lock. See the [NVDA User Guide](https://download.nvaccess.org/documentation/en/userGuide.html).
- With JAWS, press JAWSKey+Z to turn off the Virtual PC Cursor. JAWSKey is normally Insert in the desktop layout or Caps Lock in the laptop layout. See [JAWS keyboard commands](https://www.freedomscientific.com/training/jaws/hotkeys/).

The same command restores the previous mode when you need it (i.e. when arrowing through different game options).

## macOS

If you launch Steam normally, VoiceOver may find an empty window or very little usable content. The Accessible Steam shortcut starts it with the `-cef-force-accessibility` option so VoiceOver can read the interface.

In the instructions below, VO means your VoiceOver modifier: Control+Option or Caps Lock, depending on your settings.

### Use the Accessible Steam shortcut

1. Open the [Accessible Steam shortcut](https://www.icloud.com/shortcuts/229a5e53ec4d41f495b168938762acca) and choose Add Shortcut.
2. In the Shortcuts app, open Shortcuts > Settings > Advanced and turn on Allow Running Scripts.
3. Select Accessible Steam and choose File > Add to Dock.
4. If Steam is running, switch to it and quit completely with Command+Q.
5. Launch Accessible Steam from the Dock.

Use Accessible Steam whenever you start the client. You can remove the normal Steam icon from the Dock to make the two easier to distinguish.

### Choose how Steam starts at login

In Steam, open Settings with Command+Comma. Under Interface, turn off Run Steam when my computer starts so Steam does not start without the accessibility option.

If you want Steam to open after you sign in to your Mac, move the VoiceOver cursor to Accessible Steam in the Dock, press VO+Shift+M, and choose Options > Open at Login.

### Launch from Terminal instead

You can use Terminal if you prefer not to install the shortcut. Quit Steam completely with Command+Q, open Terminal, paste this command, and press Return:

```sh
open -a steam --args -cef-force-accessibility
```

This is the same command the shortcut runs. It applies when Steam starts; running it while Steam is already open does not restart the client with the option.

Continue to the Big Picture instructions below. If arrow keys move the VoiceOver cursor instead of Steam's selection, turn off arrow-key Quick Nav. On current macOS versions, VO+Shift+Q toggles it; listen for the announcement that it is off. See [Apple's Quick Nav instructions](https://support.apple.com/en-ca/guide/voiceover/vo27943/mac) for your macOS version.

## Linux with Orca

Start Orca, then open Steam normally. If Orca can read the interface, continue to the Big Picture instructions below.

If Steam's window is empty to Orca or contains little readable content, quit Steam completely and try the command for your installation in a terminal.

For Steam installed from your distribution:

```sh
steam -cef-force-accessibility
```

For the Flathub version:

```sh
flatpak run com.valvesoftware.Steam -cef-force-accessibility
```

If the command works, use it to launch Steam when needed. You can also add the option to your application-menu launcher; the steps depend on your desktop environment and how you installed Steam.

If the arrow keys read through the interface instead of moving Steam's selection, press Orca Modifier+A to switch to focus mode. Press it again to return to browse mode. The Orca modifier is normally Insert in the desktop layout or Caps Lock in the laptop layout. [GNOME documents the mode-switching command](https://help.gnome.org/orca/preferences_web.html).

## Using Big Picture Mode on Windows, macOS, and Linux

Big Picture is Steam's interface for controllers. It also supports keyboard navigation, provides screen readers with labels and control states, and uses sounds to confirm many actions.

On macOS, start Steam through Accessible Steam or the Terminal command first. Once Steam is running, open its View menu and choose Big Picture Mode.

If you cannot reach the View menu, you can open Big Picture with this address:

```text
steam://open/bigpicture
```

On Windows or Linux, paste it into your web browser's address bar and press Enter. If the browser asks to open Steam, confirm.

On macOS, launch Accessible Steam and wait for Steam to open before using the address. If Steam is closed, the address may launch it without the accessibility option. With Steam running, open Spotlight with Command+Space, paste the address, and press Return.

To use Big Picture at each launch, open Steam's Settings > Interface and turn on Start Steam in Big Picture Mode.

### Keyboard commands

- Arrow keys move between items.
- Enter, or Return on a Mac, activates the selected item or toggles a checkbox.
- Escape goes back or closes the current menu.
- Tab can reach some controls the arrow keys skip, especially on game management screens.
- Ctrl+1 opens Steam's main menu.
- Ctrl+2 opens Quick Access.

Ctrl means the Control key, including on macOS. The Ctrl+1 and Ctrl+2 shortcuts are [reported by Steam users](https://steamcommunity.com/groups/bigpicture/discussions/1/677329263114865881/); they may not work while a game is running.

If the arrow keys do not move Steam's selection, check the screen reader mode instructions in your operating system's section.

### Navigating Settings

1. Press Ctrl+1 and choose Settings.
2. Use Up and Down Arrow to select a settings category.
3. Press Right Arrow to move into that category's options.
4. Use Up and Down Arrow to move between controls, and Enter to activate the selected control.
5. Press Escape to go back.

### VoiceOver problems in Big Picture

VoiceOver may miss items in the menu opened with Ctrl+1, even when the Home feed and other screens are readable. This problem generally resolves itself, although pressing Command+tab twice to navigate in and out of the application may help.

Additionally, if full-screen Big Picture causes VoiceOver to consistently lose focus on the Steam window, switch back to the running Steam app from the Dock. In Big Picture, open Settings > Display and check the Windowed option.

## Installing, managing, and launching games

In Big Picture, open Steam's main menu with Ctrl+1 and choose Library.

1. Select a game you own.
2. Choose Install and confirm the storage location.
3. If Steam offers to create a desktop shortcut, select that option if you want one.
4. When installation finishes, look for Play to launch the game.

With Play selected, use Left and Right Arrow to reach the other actions for that game. Depending on the game and your setup, these may include Configure Controller, Play From for Remote Play, and Manage. Manage includes options such as uninstalling the game and adding it to your favorites.

The Downloads screen shows active downloads, queued updates, progress, and paused items. Steam can keep downloading while you use another application.

A desktop shortcut lets you launch a game without browsing Steam's library. You may still need to use a launch-options dialog, first-run setup tool, or separate publisher launcher, each with its own accessibility limitations. On macOS, start Accessible Steam first if Steam is closed.

## Steam Deck and SteamOS

[SteamOS has a built-in screen reader](https://steamcommunity.com/games/1675200/announcements/detail/529850584204837333) for Steam's gaming interface. On Steam Deck, press Steam+View to turn it on or off in Game Mode. Valve shows this button combination in its [accessibility announcement](https://store.steampowered.com/news/app/593110/view/824835185591718585).

You can also use Settings > Accessibility > Screen Reader. The same section has speed, pitch, and volume controls.

You do not need to install a separate screen reader or use the CEF accessibility option for Steam's Game Mode on SteamOS. Desktop Mode and individual games have separate accessibility requirements, and many games require mods that only work on Windows.

## Finding accessible games

Start with [our PC games database](https://blindgaming.net/pc) here on blindgaming.net. Open a game's page to check its accessibility notes, supported platforms, required mods, and available community ratings. Some games are only partly accessible, so read the known barriers and check whether the accessibility support works on your operating system before buying.

Steam lists accessibility features on store pages and lets you filter search results by them. [Valve defines the features developers can declare](https://partner.steamgames.com/doc/accessibility_features), including:

- Playable without Vision: players can complete the game using audio, without needing to see the screen.
- Narrated Game Menus: menus are spoken. This does not establish whether movement, combat, inventories, maps, puzzles, or other gameplay is accessible.
- Keyboard Only Option: players can use a keyboard throughout the game without a mouse or controller. This does not promise speech output.

Developers enter these claims through Steam's Accessibility Feature Wizard. [Providing this information is optional](https://store.steampowered.com/news/app/593110/view/500575275933763409), so a missing label does not establish that a game is inaccessible. Before buying, check player reports for your operating system and game version, especially if accessibility depends on a mod or a separate launcher.

For additional context, consult the [Accessible Gaming Wiki's blind-accessible games list](https://accessiblegaming.wiki/Blind_accessible_games) or search [AudioGames.net](https://forum.audiogames.net/) for recent experiences from blind players. Use these as supplementary sources when you need more detail about a game or its current version.

When trying a game, check these points early:

- Can you get through the launcher and first-run setup without sighted help?
- Can you enable accessibility from the first screen?
- Can you use both the menus and the gameplay?
- Can you change settings, save, and load?
- Does accessibility still work after restarting the game?

For games bought directly from the Steam Store, [the standard refund conditions](https://store.steampowered.com/steam_refunds/) are a request within 14 days of purchase and less than two hours of playtime. Check for setup and launcher barriers early so you have time to assess gameplay within those limits. Steam also considers requests outside those conditions, but approval is not assured.

## Credits

Thanks to [Piotr Machacz](https://dragonscave.space/@pitermach) for sharing the macOS accessibility workaround and creating the Accessible Steam shortcut. His [original AudioGames.net post](https://forum.audiogames.net/topic/56637/how-to-make-steam-accessible-on-mac-and-big-picture-mode-improvements/) describes the Mac setup and Big Picture improvements.

Valve's [October 25, 2023 client update](https://steamcommunity.com/games/593110/announcements/detail/3723973446147960644) documents the `-cef-force-accessibility` option under Linux. It forces accessibility support in steamwebhelper, the part of Steam's interface that uses Chromium Embedded Framework. The macOS workaround uses that same option.

Thanks also to the Steam Community contributors documenting keyboard shortcuts, and the Accessible Gaming Wiki and AudioGames.net communities for their game accessibility reports. Platform and screen reader references are linked alongside the instructions.
