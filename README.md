# Butterflies

Eight butterflies on the Linux desktop. They fly at the same time. The windows are click-through, except for the butterfly itself. Point at one and it turns away.

The choice bar stays hidden until a butterfly is clicked, then it closes shortly after the last choice.

## Install

Needs Python 3, GTK 3, PyGObject, and cairo (`python3-gi`, `python3-gi-cairo`, `gir1.2-gtk-3.0`).

```bash
git clone https://github.com/burnt1983/butterflies.git
cd butterflies
bash install.sh
butterflies
```

The pictures are stored in this repository as text parts and unpacked by `install.sh` into `~/.local/share/lee-insects/sprites/`. Place and which butterflies are hidden are saved in `~/.config/lee-insects/config.json`. It does not start at login until you tick Open at login.

## How to use it

| Action | What happens |
|---|---|
| Click a butterfly | Open the choice bar. It pauses as the pointer arrives |
| Click a thumbnail | Hide or show that butterfly |
| Scroll the bar | Resize |
| Drag the bar | Move the bar |
| Right-click the bar | Stay above, show all, hide all, calm / natural / lively, open at login, quit |

## Uninstall

```bash
bash uninstall.sh
```

## Licence

MIT. The butterfly pictures are part of this project. See [LICENSE](LICENSE).
