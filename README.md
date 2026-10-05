# IPTV EPG

Guía XMLTV consolidada para Flex IPTV.

## Guía activa en Flex

La guía confirmada en uso por Flex IPTV es:

```text
https://raw.githubusercontent.com/Chevosky/IPTV/main/guide-flex-20261003.xml
```

`guide-flex-20261003.xml` es el nombre estable que usamos en Flex. Los workflows de EPG curado la refrescan a partir de `guide-favorites.xml`.

## Cobertura inicial

- USA
- LATAM: Argentina, Brasil, Chile, Colombia, Costa Rica, República Dominicana, Ecuador, México, Panamá, Perú y Uruguay
- Europa: Reino Unido, España, Francia, Italia, Alemania, Portugal, Países Bajos, Bélgica y Suiza
- FAST: Plex, Samsung TV Plus y Rakuten en varios mercados

La guía base se genera desde fuentes públicas de EPGShare01 y se reduce a una ventana de 48 horas. Luego la guía curada incorpora los mapeos y fuentes exactas usados por la lista de favoritos.

## Automatización

- `guide.xml`: guía consolidada base, regenerada por `.github/workflows/update-epg.yml`.
- `guide-favorites.xml`: salida curada de trabajo.
- `guide-flex-20261003.xml`: guía confirmada para Flex; los workflows curados la actualizan junto con `guide-favorites.xml`.

## Archivos principales

- `sources.json`: fuentes de la guía base.
- `scripts/build_epg.py`: construye `guide.xml`.
- `scripts/build_favorites_guide.py`: construye la guía curada.
- `.github/workflows/update-epg.yml`: actualización de la guía base.
- `.github/workflows/update-curated-epg.yml`: actualización de la guía curada y de la guía confirmada de Flex.
- `.github/workflows/update-exact-iptvorg-epg.yml`: incorpora EPG exacta y vuelve a publicar la guía curada.
