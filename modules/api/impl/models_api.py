from fastapi import HTTPException

from modules.api import utils as api_utils
from modules.api.Api import APIManager
from modules.core.models import zoo


def setup(app: APIManager):

    @app.get("/v1/models/reload", response_model=api_utils.BaseResponse)
    async def reload_models():
        zoo.model_zoo.reload_all_models()
        return api_utils.success_response("Models reloaded")

    @app.get("/v1/models/unload", response_model=api_utils.BaseResponse)
    async def unload_models():
        zoo.model_zoo.unload_all_models()
        return api_utils.success_response("Models unloaded")

    @app.get("/v1/models/list", response_model=api_utils.BaseResponse)
    async def unload_models():
        raise HTTPException(status_code=501, detail="Not implemented")
