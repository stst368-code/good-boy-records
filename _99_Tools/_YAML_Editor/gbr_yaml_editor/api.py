"""Routes mounted by the shared application, with its existing authentication."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from .service import Editor


class Body(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Scan(Body):
    directory: str | None = None
    recursive: StrictBool = True


class File(Body):
    directory: str
    path: str


class Preview(Body):
    directory: str
    files: list[str] = Field(min_length=1, max_length=2000)
    operations: list[dict] | None = None
    text: str | None = Field(default=None, max_length=90000)
    expected: dict[str, str] = Field(default_factory=dict)


class Apply(Body):
    token: str
    skip_errors: StrictBool = False


class Restore(Body):
    batch: str


def router(repo_root, local):
    editor = Editor(repo_root)

    def host_only(request: Request):
        if not local(request):
            raise HTTPException(403, 'Open the YAML editor on the host computer, as with folder settings')

    routes = APIRouter(prefix='/api/yaml-editor', dependencies=[Depends(host_only)])

    @routes.post('/scan')
    def scan(body: Scan):
        return editor.scan(body.directory, body.recursive)

    @routes.post('/file')
    def file(body: File):
        return editor.read(body.directory, body.path)

    @routes.post('/preview')
    def preview(body: Preview):
        return editor.preview(body.directory, body.files, body.operations, body.text, body.expected)

    @routes.post('/apply')
    def apply(body: Apply):
        return editor.apply(body.token, body.skip_errors)

    @routes.get('/history')
    def history():
        return {'batches': editor.history(), 'retention': 20}

    @routes.post('/restore-preview')
    def restore_preview(body: Restore):
        return editor.restore_preview(body.batch)

    return routes, editor
