from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import tiles, export, pointcloud, volume

app = FastAPI(
    title="G20 Tech Geospatial Service",
    description="Raster tile serving, COG processing, and point cloud export for WebODM",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tiles.router, prefix="/tiles", tags=["tiles"])
app.include_router(export.router, prefix="/export", tags=["export"])
app.include_router(pointcloud.router, prefix="/pointcloud", tags=["pointcloud"])
app.include_router(volume.router, prefix="/volume", tags=["volume"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "webodm-geospatial"}
