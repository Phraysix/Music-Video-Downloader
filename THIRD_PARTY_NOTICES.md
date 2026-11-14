# Third-party components

Müzik İndirici uses the open-source projects below. The ready-made `MuzikIndirici.exe`
bundles some of them. See each project's own page for the full license text.

| Component | Used for | License | Source |
|---|---|---|---|
| yt-dlp | Download engine | Unlicense | https://github.com/yt-dlp/yt-dlp |
| yt-dlp-ejs | YouTube challenge solver (a yt-dlp component) | See the project page | https://github.com/yt-dlp/ejs |
| FFmpeg (ffmpeg, ffprobe) | Audio/video conversion and merging | LGPL or GPL (depends on the build) | https://ffmpeg.org/legal.html |
| Deno | JavaScript runtime (required for YouTube) | MIT | https://github.com/denoland/deno |
| CustomTkinter | User interface | MIT | https://github.com/TomSchimansky/CustomTkinter |
| PyInstaller | Packaging the exe | GPL-2.0-or-later (with an exception for bundled applications) | https://pyinstaller.org |

## About FFmpeg

The FFmpeg binaries in the ready-made exe come from the "release essentials" build at
https://www.gyan.dev/ffmpeg/builds/, which is licensed under GPL v3. The FFmpeg source code
and build information are available from the links above. The FFmpeg project is not
affiliated with this software.
