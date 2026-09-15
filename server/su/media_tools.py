"""Tool specs and dispatch for the extra media tools: edit_image, generate_speech, transcribe, generate_diagram."""
from typing import Dict, Optional

from .audio import generate_speech, transcribe
from .config import _tool
from .diagram import generate_diagram
from .media import generate_image

TOOLS = [
    _tool("edit_image", "Edit an existing image with the image model: the prompt says what to change, source is the image to edit.",
          {"prompt": {"type": "string"}, "source": {"type": "string", "description": "Workspace path of the image to edit"},
           "path": {"type": "string", "description": "Optional output path (.png)"}}, ["prompt", "source"]),
    _tool("generate_speech", "Text to speech saved as an mp3 in the workspace (SU_SPEECH_MODEL).",
          {"text": {"type": "string"}, "path": {"type": "string", "description": "Optional output path (.mp3)"}, "voice": {"type": "string"}}, ["text"]),
    _tool("transcribe", "Speech to text for an audio or video file in the workspace (SU_TRANSCRIBE_MODEL). Saves the transcript next to the file.",
          {"path": {"type": "string"}, "language": {"type": "string", "description": "ISO code, default auto"}}, ["path"]),
    _tool("generate_diagram", "Render a block diagram from D2 source to an SVG file.",
          {"code": {"type": "string", "description": "D2 source"}, "path": {"type": "string", "description": "Optional output path (.svg)"}}, ["code"]),
]


async def handle(name: str, args: Dict) -> Optional[str]:
    try:
        if name == "edit_image":
            return await generate_image(args["prompt"], args.get("path"), args["source"])
        if name == "generate_speech":
            return await generate_speech(args["text"], args.get("path"), args.get("voice"))
        if name == "transcribe":
            return await transcribe(args["path"], args.get("language"))
        if name == "generate_diagram":
            return await generate_diagram(args["code"], args.get("path"))
    except RuntimeError as e:
        return str(e)
    return None
