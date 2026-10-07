FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GEO_DATABASE_URL=sqlite:////data/geo.db

WORKDIR /srv

# shapely, pyproj and pyogrio ship manylinux wheels that bundle GEOS, PROJ and GDAL,
# so no system geospatial libraries are needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY samples ./samples

RUN useradd --create-home --uid 1000 geo && mkdir /data && chown geo /data
USER geo
VOLUME /data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
