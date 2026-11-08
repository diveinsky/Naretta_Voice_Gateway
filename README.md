# Naretta

**Naretta** (pronounced *nah-RET-tah*) is a Windows desktop voice chatbot for everyday
conversation. Click **Start conversation**, talk, and Naretta answers out loud. If you talk over
her, she stops at once and listens.

Built with Python, PySide6 and the OpenAI Realtime API (speech-to-speech over WebSocket,
model `gpt-realtime-2.1`; your words are transcribed for the chat with `gpt-4o-transcribe`).

## Requirements

- Windows 10 or 11
- Python 3.10–3.13 (64-bit) from [python.org](https://www.python.org/downloads/windows/)
- A microphone and speakers or headphones (headphones recommended, see [Testing with headphones](#testing-with-headphones))
- An OpenAI API key with access to the Realtime API

## Setup (Windows, PowerShell)

```powershell
cd path\to\Naretta_Voice_Gateway
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

If PowerShell blocks `Activate.ps1`, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or use `.venv\Scripts\activate.bat` from `cmd`.

## Adding your API key

Choose one:

1. **Settings (recommended).** Open **Settings**, paste the key into the masked field and click
   **Save**. It goes into Windows Credential Manager (generic credential `Naretta`) and loads
   automatically on the next launch. **Remove saved key** deletes it.
2. **Environment variable.** Set `OPENAI_API_KEY` for your user:
   `setx OPENAI_API_KEY "sk-..."` (then open a new terminal), or copy `.env.example` to `.env`
   next to `main.py` / `Naretta.exe` and fill it in.

A key saved in Settings takes priority over `OPENAI_API_KEY`. The key is never written to source
files, settings, or the log, and it is removed from error messages.

## Using Naretta

Naretta opens as a round window. Drag anywhere on the circle to move it. The small buttons at
the top are **Settings**, **Background** (sun/moon: dark or white), **Orb-only view**,
**Minimize** and **Close**. The orb-only view hides the chat and controls so the animation fills the
circle; click the button again or double-click the orb to bring them back.

| Control | What it does |
| --- | --- |
| **Start conversation** (big round button, or click the animated circle) | Opens the mic and speaker and connects to OpenAI. The button then becomes **Stop**. |
| **Stop** | Disconnects and releases the audio devices. The conversation is kept. |
| **Mute microphone** (left button) | Stops sending your audio. Naretta can still finish speaking. |
| **New conversation** (right button) | Forgets everything and starts fresh. Naretta introduces herself again. |
| **Settings** (gear at the top) | Microphone, speaker, setup type, voice, animation style and API key. |

The status pill shows **Connecting**, **Listening**, **Speaking**, **Muted**, **Reconnecting** or
**Disconnected**. The animation reacts to your voice while Naretta listens (teal, turning green as
you speak) and to her voice while she speaks (purple). **Settings › Animation** switches between
*Orb*, *Rings* and *Bars*. Transcripts appear in the chat panel as they arrive.

- **Listening modes** (Settings › Listening):
  - *Always listening* (default): after **Start**, Naretta answers whenever you finish speaking.
  - *Wake word "Naretta"*: after **Start**, Naretta waits (indigo, "Say 'Naretta' to wake me").
    Say a sentence with her name, such as "Hey Naretta, what's a good movie?", and she answers
    it, then keeps conversing until about 20 seconds of quiet. Clicking the orb also wakes her.
    The wake word is spotted by OpenAI's transcription, so audio is still streamed while she
    waits; sentences without her name are deleted from the conversation and never shown.
- **Your language** (Settings): the language used to write your words in the chat. Naretta
  herself listens to your voice and replies in whatever language you speak; this setting only
  stops the transcript from guessing the wrong language on short phrases.
- **Interrupting:** start talking while Naretta speaks. Playback stops at once and queued audio is
  thrown away. Naretta then tells OpenAI how much of the reply you actually heard
  (`conversation.item.truncate`), so her memory matches what you heard. The chat bubble is marked
  *Interrupted* and trimmed to an estimate of what was heard. The API cuts the audio but not the
  text transcript, so the trimmed text is approximate.
- **Memory:** the conversation lasts until **New conversation**. After **Stop**/**Start**, a
  dropped connection, or a settings change, Naretta replays the transcript into the new session,
  so she still remembers what you said.
- **Settings while connected:** saving Settings reconnects with the new devices and voice, and
  keeps the conversation.
- Settings other than the key are stored in `HKCU\Software\Naretta`. Logs are in
  `%LOCALAPPDATA%\Naretta\naretta.log`.

## Echo cancellation

**Settings › Echo cancellation and voice cleanup** (on by default) runs your microphone through
WebRTC's audio processing (via the `livekit` package): it subtracts what Naretta is playing from
what the mic hears and reduces background noise. It's what keeps her from
interrupting herself when you use speakers. Laptops with built-in voice processing (for example
Intel Smart Sound microphone arrays) already filter some of this in hardware; leaving the
option on is harmless there.

`Naretta.exe --self-test` checks that the audio devices and echo canceller load and writes the
result to the log.

## Testing with headphones

When Naretta plays through speakers, the microphone can pick up her voice. The API's voice
detection may then take it for you talking, so she stops herself mid-sentence. This is a "false
interruption".

- **Best:** test with headphones or a headset. In **Settings**, choose that microphone and
  speaker and set **Setup** to *Headphones or headset microphone*.
- **If you must use speakers:** set **Setup** to *Speakers with laptop or desk microphone*. This
  enables the API's far-field noise reduction. Also keep the volume moderate and the mic away
  from the speakers. If your audio driver offers echo cancellation (in *Sound settings ›
  Microphone › Audio enhancements* or the vendor's app), turn it on.
- **Quick check:** with headphones, ask a question that needs a long answer and say "wait" halfway
  through. Naretta should go quiet within a fraction of a second and answer your interruption.
  Then try the same through speakers. If she interrupts herself with nobody talking, that is
  echo, and headphones are the fix.

## Building `Naretta.exe`

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
.\build.ps1
```

`build.ps1` runs:

```powershell
pyinstaller --noconfirm --clean --onefile --windowed --name Naretta `
  --hidden-import keyring.backends.Windows --collect-submodules win32ctypes --collect-all livekit `
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets `
  --exclude-module PySide6.Qt3DCore --exclude-module tkinter main.py
```

The result is `dist\Naretta.exe`. It's a single file, and Windows may take a few seconds to
start it the first time while it unpacks.

## Troubleshooting

| Message | What to do |
| --- | --- |
| *OpenAI rejected the API key* | Paste a valid key in Settings (check for stray characters). |
| *doesn't have access to the realtime model* | The key's project needs Realtime access; check project permissions and billing. |
| *rate limit or quota was exceeded* | Check your OpenAI plan and usage. |
| *Can't reach OpenAI* / *Reconnecting…* | Check the network or proxy. Naretta retries 3 times, keeping the conversation. |
| *isn't receiving any sound from the microphone* | Turn on **Settings › Privacy & security › Microphone › Let desktop apps access your microphone**, and check the mic isn't muted in Windows. |
| *The selected microphone … isn't available* | Plug it in or choose another device in Settings. |
| Naretta sounds quiet | OpenAI's voice audio peaks around -12 dBFS. Raise **Settings › Voice volume** (300% is the default, 400% the maximum; peaks are soft-limited) and check Naretta's slider in Windows **Volume mixer**. |
| Naretta interrupts herself | Echo from speakers. See [Testing with headphones](#testing-with-headphones). |

Set `NARETTA_DEBUG=1` for detailed logs. `NARETTA_MODEL` and `NARETTA_TRANSCRIBE_MODEL` override
the default models.

## Licensing (Qt / PySide6)

Naretta uses **PySide6** (Qt for Python), which is free to use, including commercially, under the
**GNU LGPL v3**. No paid Qt license is needed. The app uses only the LGPL modules QtCore, QtGui and
QtWidgets. The LGPL's conditions apply when you **distribute** `Naretta.exe` to other people:

- Keep Qt and PySide6 unmodified and dynamically linked (they are, as separate DLLs/.pyd files).
- Let recipients replace the Qt libraries with their own build. A folder build
  (`pyinstaller --onedir ...`) makes this straightforward, because the Qt DLLs sit next to the
  exe. A `--onefile` build bundles them into the exe; to stay safe, also offer the folder build
  or this source code.
- Include the LGPL v3 text and a notice that the app uses Qt/PySide6 (Qt is available from
  qt.io). Note the license in an About screen or the readme.

Running it yourself, or within your own company, doesn't trigger these obligations. This is a
summary, not legal advice. If you plan to sell Naretta, check the Qt licensing FAQ at
qt.io/licensing.

The other dependencies use permissive licenses: livekit (Apache 2.0; bundles WebRTC, BSD), websockets (BSD), sounddevice (MIT; it bundles
PortAudio, MIT), numpy (BSD), keyring (MIT), python-dotenv (BSD).

## Development

```powershell
pip install -r requirements-dev.txt
python -m pytest
```

The tests run the engine against a local fake Realtime server with fake audio devices. They cover
the session config, mic streaming, barge-in truncation, discarding stale audio, the invalid-key
path, reconnecting with history replay, and new conversations. They also include offscreen UI
smoke tests.

| File | Purpose |
| --- | --- |
| `naretta/realtime.py` | WebSocket session, event handling, interruption, reconnects (asyncio on a background thread) |
| `naretta/audio.py` | Mic capture and speaker playback (sounddevice/PortAudio), resampling, played-time tracking |
| `naretta/history.py` | Local transcript used for display and replay |
| `naretta/credentials.py` | Windows Credential Manager + `OPENAI_API_KEY` |
| `naretta/redact.py` | Strips keys from logs and messages |
| `naretta/ui/` | Main window, chat panel, settings dialog |
