# IPTV EPG

Guía XMLTV consolidada para Flex IPTV.

## Cobertura inicial

- USA
- LATAM: Argentina, Brasil, Chile, Colombia, Costa Rica, República Dominicana, Ecuador, México, Panamá, Perú y Uruguay
- Europa: Reino Unido, España, Francia, Italia, Alemania, Portugal, Países Bajos, Bélgica y Suiza
- FAST: Plex, Samsung TV Plus y Rakuten en varios mercados

La guía se genera desde fuentes públicas de EPGShare01 y se reduce a una ventana de 48 horas para mantener un XML manejable para Flex IPTV.

## URL para Flex IPTV

```text
https://raw.githubusercontent.com/Chevosky/IPTV/main/guide.xml
```

## Actualización

GitHub Actions regenera `guide.xml` automáticamente todos los días y también cuando se modifican la configuración o el script.

## Archivos

- `sources.json`: fuentes incluidas
- `scripts/build_epg.py`: descarga, descomprime, filtra, deduplica y fusiona XMLTV
- `.github/workflows/update-epg.yml`: automatización diaria
- `guide.xml`: salida para Flex IPTV
