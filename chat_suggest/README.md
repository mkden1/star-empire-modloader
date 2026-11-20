# Chat suggestions for /ap

Suggests system names while you type `/ap` in the chat.

## What it does

- Type `/ap ` (or `/ap Alp`) and a list opens above the chat line with the systems the autopilot can fly to, best match first.
- Your favourite systems (the galaxy map's Favorites, in your order) come first, marked with a star. Within each group a name that starts with what you typed beats one with a word that starts with it, which beats one that merely contains it.
- Only systems `/ap` would really route to are listed (explored, connected to where you are, not the system you are in), each with its jump count. One beyond your Stellar Navigation limit is shown dimmed.

## Using it

- **Tab** completes the line with the highlighted system (the first one if none is highlighted).
- **Down / Up** move the highlight.
- **Enter** with a highlighted system completes it and sends; with none it sends what you typed, as always.
- A click on a row completes it. With no list open the chat line behaves exactly as before.

## Settings

- `STAR_EMPIRE_NO_CHAT_SUGGEST=1` turns it off.

## Needs

Nothing else.

## Under the hood

- Patches the game's `Client.py` (3 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `chat_suggest.py`.
