"""REQ-1.2: the Azure Functions entry point wraps the FastAPI app via AsgiFunctionApp."""

import azure.functions as func

import function_app
from app.main import app as fastapi_app


def test_asgi_adapter_wraps_app():
    # function_app.app is the Azure Functions app object the runtime discovers.
    assert isinstance(function_app.app, func.AsgiFunctionApp)


def test_adapter_exposes_the_fastapi_app():
    # The same FastAPI instance is what the adapter serves.
    assert function_app.fastapi_app is fastapi_app
