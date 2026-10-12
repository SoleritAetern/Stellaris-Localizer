# About dialog icons

The application uses `app.ico` / `app.png` for its logo. About uses these supplied
monochrome SVGs for its icon-only buttons, with instant captions and accessible names:

- `github.svg`
- `guide_book.svg`
- `discord.svg`

`resources.themed_svg_icon()` fits their painted bounds to a common 18 px canvas
and applies semantic icon colors at render time for dark-theme contrast.
Original SVGs are not changed. This opt-in loader does not recolor the app logo
or other decorative images. No external assets are downloaded by the app.
The existing PyInstaller specification includes this directory via `assets`.

Public URLs, including the Discord invitation, are configured in `src/app_info.py`.
