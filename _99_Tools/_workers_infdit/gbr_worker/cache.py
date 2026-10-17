"""Content-addressed, checksummed safetensors; never deserialize pickle caches."""
import json
from pathlib import Path
import shutil
import tempfile

from .contract import canonical, file_sha


def validate_tensor(tensor, torch):
    if tensor.ndim != 3 or tensor.shape[0] != 1 or not 0 < tensor.shape[1] <= 9000 or tensor.shape[2] != 32768:
        raise ValueError("Invalid native conditioning tensor dimensions")
    if tensor.dtype not in (torch.bfloat16, torch.float32) or not torch.isfinite(tensor).all().item():
        raise ValueError("Invalid native conditioning tensor values/dtype")


def load(root, key, identity, torch):
    from safetensors import SafetensorError
    from safetensors.torch import load_file
    directory = Path(root) / key
    if not directory.is_dir():
        return None, None
    record = json.loads((directory / "cache.json").read_text(encoding="utf-8"))
    if not isinstance(record, dict) or not {"identity", "sha256", "origin"}.issubset(record) or not isinstance(record["origin"], dict):
        raise ValueError("Invalid conditioning cache manifest")
    if record["identity"] != identity or file_sha(directory / "conditioning.safetensors") != record["sha256"]:
        raise ValueError("Conditioning cache identity/checksum mismatch")
    try:
        tensors = load_file(str(directory / "conditioning.safetensors"), device="cpu")
    except SafetensorError as exc:
        raise ValueError("Invalid conditioning safetensors file") from exc
    if set(tensors) != {"frame_hiddens"}:
        raise ValueError("Unexpected conditioning cache tensors")
    value = tensors["frame_hiddens"]
    validate_tensor(value, torch)
    expected = torch.bfloat16 if identity["parameters"]["text_encoder"] == "bf16" else torch.float32
    if value.dtype != expected or value.shape[1] > min(9000, int(identity["parameters"]["duration"] * 25)):
        raise ValueError("Conditioning cache dtype/frame limit mismatch")
    return value, record


def save(root, key, identity, tensor, provenance):
    from safetensors.torch import save_file
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    directory = root / key
    staging = Path(tempfile.mkdtemp(prefix="encoding-", dir=root))
    try:
        save_file({"frame_hiddens": tensor.contiguous()}, str(staging / "conditioning.safetensors"))
        record = {"identity": identity, "sha256": file_sha(staging / "conditioning.safetensors"),
                  "origin": provenance}
        (staging / "cache.json").write_text(canonical(record) + "\n", encoding="utf-8")
        # A valid existing entry can be shared; corrupted entries are not overwritten silently.
        if directory.exists():
            return
        try:
            staging.rename(directory)
        except FileExistsError:
            pass
    finally:
        if staging.exists():
            shutil.rmtree(staging)
