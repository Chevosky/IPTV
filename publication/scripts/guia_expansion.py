"""Refresh only expansion schedules bound to an exact verified signal."""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timedelta, timezone
import gzip
from html import unescape
from html.parser import HTMLParser
from io import BytesIO
import json
import re
from unicodedata import normalize
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

from filter_expansion import VERIFIED_STATES, programme_state

XMLTV = "%Y%m%d%H%M%S %z"
ROME = ZoneInfo("Europe/Rome")
LONDON = ZoneInfo("Europe/London")
MADRID = ZoneInfo("Europe/Madrid")
QUITO = ZoneInfo("America/Guayaquil")
SANTIAGO = ZoneInfo("America/Santiago")
SANTO_DOMINGO = ZoneInfo("America/Santo_Domingo")
KAMPALA = ZoneInfo("Africa/Kampala")
BRASILIA = ZoneInfo("America/Sao_Paulo")
BERLIN = ZoneInfo("Europe/Berlin")
SEOUL = ZoneInfo("Asia/Seoul")
ATHENS = ZoneInfo("Europe/Athens")
MEXICO_CITY = ZoneInfo("America/Mexico_City")
PANAMA = ZoneInfo("America/Panama")
WARSAW = ZoneInfo("Europe/Warsaw")
JAKARTA = ZoneInfo("Asia/Jakarta")
BOGOTA = ZoneInfo("America/Bogota")
LA_PAZ = ZoneInfo("America/La_Paz")
LIMA = ZoneInfo("America/Lima")
CASABLANCA = ZoneInfo("Africa/Casablanca")
BUENOS_AIRES = ZoneInfo("America/Argentina/Buenos_Aires")
NEW_YORK = ZoneInfo("America/New_York")
BANGKOK = ZoneInfo("Asia/Bangkok")
MUSCAT = ZoneInfo("Asia/Muscat")
TUNIS = ZoneInfo("Africa/Tunis")
LISBON = ZoneInfo("Europe/Lisbon")
ERBIL = ZoneInfo("Asia/Baghdad")
DOHA = ZoneInfo("Asia/Qatar")
BELGRADE = ZoneInfo("Europe/Belgrade")
TVL_ECUADOR = "TVLegislativa.ec@SD"
TVL_STREAM = "https://streamingav.asambleanacional.gob.ec/hls/stream.m3u8"
APUNT_LIVE = "https://www.apuntmedia.es/directe/directe-tv_136_1392524.html"
APUNT_ACCOUNT = "6057955885001"
APUNT_WEB_SCHEDULE = "https://api.apuntmedia.es/programacio/internet/"
ERTNEWS_GUIDE = "https://www.ert.gr/tv/program/ertnews/"
MEDIASET_GUIDE = "https://api-ott-prod-fe.mediaset.net/PROD/play/alive/allListingFeedEpg/v1.0"
ONCE_GUIDE = "https://canaloncetv.s3.amazonaws.com/REST/data/mdb/ec2/cartelera_mensual.json"
TELEMETRO_GUIDE = "https://www.telemetro.com/parrilla"
TVP_INFO_GUIDE = "https://www.tvp.pl/program-tv/tvp-info"
RED_UNO_GUIDE = "https://www.reduno.com.bo/programacion"
RED_UNO_SCZ_TARIFF = "https://comercial.reduno.com.bo/app/tarifario.php?id_tarifario=1"
TC_GUIDE = "https://tctelevision.com/programacion/"
TC_TABLE = ("https://docs.google.com/spreadsheets/d/1I8qvah7obnrFhVRqc5zzCKqCFgny8vsNSnJLEqmGMrY/"
            "gviz/tq?tqx=out:csv&sheet=programacion&range=B3:AQ40&headers=0")
SHAHID_GUIDE = "https://api2.shahid.net/proxy/v2.1/shahid-epg-api/"
ASHARQ_NOW_GUIDE = "https://api-now.asharq.com/api/channels/{}/epg/{}"
MEO_GUIDE = ("https://meogouser.apps.meo.pt/Services/GridTv/GridTv.svc/"
             "GetLiveChannelProgramsByDate")
MEO_HEADERS = {"Origin": "https://www.meo.pt", "Referer": "https://www.meo.pt/"}
MEO_AGENT = ("Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Mobile Safari/537.36")
CHADA_GUIDE = "https://chada.ma/fr/programmes/?channel=tv"
NTV_GUIDE = "https://nusantaratv.com/berita/jadwal-program-nusantara-tv"
CARACOL_GUIDE = "https://www.caracoltv.com/programacion-internacional"
MAGHRIBIA_GUIDE = "https://www.snrt.ma/ar/node/4072"
ALLEGRO_GUIDE = "https://allegrospatial.com/es/grille.json"
ESKA_GUIDE = "https://www.eska.tv/tv-html/module/page{}/"
STAR_GREECE_GUIDE = "https://www.star.gr/tv/$component/ProgramProgramRows"
FORMULA_GUIDE = "https://www.formulatv.com/{market}/programacion/"
SAMSUNG_CA_XMLTV = "https://i.mjh.nz/SamsungTVPlus/ca.xml.gz"
SAMSUNG_DE_XMLTV = "https://i.mjh.nz/SamsungTVPlus/de.xml.gz"
SAMSUNG_GB_XMLTV = "https://i.mjh.nz/SamsungTVPlus/gb.xml.gz"
TUBI_XMLTV = ("https://raw.githubusercontent.com/BuddyChewChew/tubi-scraper/"
                       "refs/heads/main/tubi_epg.xml")
PLEX_AU_XMLTV = "https://i.mjh.nz/Plex/au.xml.gz"
PLEX_AU_NAMES = {"CricketGold.au@SD": "Cricket Gold", "BloombergTV.us@US": "Bloomberg TV"}
CBS_RUNDOWN = "https://feeds-cbsn.cbsnews.com/2.1/rundown/"
CBS_MIAMI_GUIDE = "https://pluto.tv/us/watch/live-tv/31218/"
CBS_NATIONAL_GUIDE = "https://epg.pw/api/epg.xml"
EUROPA_EUROPA_GUIDE = "https://europaeuropa.tv/programacion/"
AYN_GUIDE = "https://ayn.om/schedule/"
AYN_NAMES = {"158": "قناة عمان العامة", "160": "قناة عمان الثقافية", "161": "قناة عمان مباشر"}
AYN_WEEKDAYS = ("الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد")
DMAX_GUIDE = "https://dmax.it/guida-tv/"
DMAX_SCHEDULE = "https://public.aurora.enhanced.live/site/page/guida-tv?" + urlencode({
    "include": "default", "filter[environment]": "dmaxit", "v": 2,
})
MONTHS_ES = ("enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre")
MONTHS_ID = ("Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus",
             "September", "Oktober", "November", "Desember")
WEEKDAYS_ID = ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu")
WEEKDAYS_ES = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
RAI = {
    "Rai2.it@SD": ("rai-2", "Rai 2"),
    "Rai3.it@HD": ("rai-3", "Rai 3"),
    "Rai5.it@SD": ("rai-5", "Rai 5"),
    "RaiMovie.it@HD": ("rai-movie", "Rai Movie"),
}
# Each XMLTV edition was matched against the broadcaster's listings or exact live signal.
EPGSHARE = {
    "bTV.bg@SD": ("BG1", "bTV.bg", "bTV"),
    "3CatInfo.es@SD": ("ES1", "3CatInfo.es", "3CatInfo"),
    "CMMTV.es@HD": ("ES1", "Castilla-La.Mancha.Media.es", "Castilla-La Mancha Media"),
    "TVBrasil.br@SD": ("BR2", "TV.Brasil.br", "TV Brasil"),
    "TVCultura.br@SD": ("BR2", "TV.Cultura.br", "TV Cultura"),
    "CTSport.cz@SD": ("CZ1", "ČT.sport.cz", "ČT sport"),
    "CT24.cz@SD": ("CZ1", "ČT24.cz", "ČT24"),
    "FoxSportsNews.au@SD": ("AU1", "FoxSportsNews.au", "Fox Sports News"),
    "DeluxeMusic.de@SD": ("DE1", "DELUXE.MUSIC.de", "DELUXE MUSIC"),
    "ServusTV.at@SD": ("AT1", "SERVUS.TV.at", "SERVUS TV"),
    "TVRInfo.ro@SD": ("RO2", "TVR.Info.ro", "TVR Info"),
    "DDSports.in@SD": ("IN4", "DD.Sports.in", "DD Sports"),
    "TVRI.id@HD": ("ID1", "TVRI.id", "TVRI"),
    "ClassCNBC.it@SD": ("IT1", "Class.CNBC.it", "Class CNBC"),
    "HorseTV.it@SD": ("IT1", "Horse.TV.HD.it", "Horse TV HD"),
    "RecordNews.br@SD": ("BR2", "Record.News.br", "Record News",
                        "https://rnw-rn.otteravision.com/rnw/rn/rnw_rn.m3u8"),
}
SYDNEY_XMLTV = "https://i.mjh.nz/au/Sydney/epg.xml.gz"
SYDNEY = {
    "ABCTV.au@NSW": ("abc-nsw", "ABC TV"),
    "ABCEntertains.au@Sydney": ("abc-me", "ABC Entertains"),
}
MEDIASET = {
    "Boing.it@HD": ("KB", "Boing", "boing.akamaized.net", "/6380392800112/eu-central-1/6284318116001/"),
    "Cartoonito.it@HD": ("LA", "Cartoonito", "boing.akamaized.net", "/6380673432112/eu-central-1/6284318116001/"),
    "MediasetItalia.it@SD": ("MY", "Mediaset Italia | Europa e Africa", "d3k8wzt41aflvx.cloudfront.net", "/MediasetEU/"),
    "TGCom24.it@SD": ("KF", "TGCom", "d3k8wzt41aflvx.cloudfront.net", "/TGCOM24/"),
}
CANAL_13_GUIDES = {"13festival": ("13Festival", "1844"), "13realities": ("13Realities", "1814"),
                   "13t": ("13Teleseries", "1786")}
AMERICA_TV_GUIDES = {
    "americatvguide-gt": ("/es/gt/c/canal-13", "canal13-guatemala.html", ZoneInfo("America/Guatemala"),
                         "GMT-6 (Guatemala)", None, {"fin de transmisión"}),
    "americatvguide-co": ("/es/co/c/city-tv", "citytv-bogota.html", BOGOTA,
                         "GMT-5 (Colombia)", 'href="https://citytv.eltiempo.com/"',
                         {"fuera del aire", "programación comercial"}),
}
WONDER_UK_GUIDE = "https://www.filmtheque.com/en/guide/channel/wonder-uk"
OTV_LEBANON_GUIDE = "https://streamingtvguides.com/Channel/OTVLB"
TICKER_NEWS_STREAM = "https://live-hls-7agy.livepush.io/live_abr_cdn/nsitWAl1pTZtwca/emnmZbKZcO8RfwJR/index.m3u8"
MEGA_FICCION_GUIDE = "https://pantallazo.cl/programacion-tv/mega-ficcion/?epg_json=megaficcion"
OFFICIAL_STREAMS = {
    "MegaFiccion.cl@SD": ("pantallazo", "megaficcion",
                          "http://15.204.196.147:22880/MEGAFICCION-MEGAMEDIA-EE.UU-PROVEEDOR/index.m3u8"),
    "tickerNews.au@SD": ("ticker-news", "ticker-news", TICKER_NEWS_STREAM),
    "LoveNature.ca@SD": ("tubi-love-nature", "700418", "https://aegis-cloudfront-1.tubi.video/6d6d0f24-8445-4b4c-bdf6-44f9e38beaa4/playlist.m3u8"),
    "TVSenado.cl@SD": ("senado-chile", "tv-senado", "https://janus-tv-ply.senado.cl/playlist/playlist.m3u8"),
    "AlArabyTV.qa@SD": ("osn", "3800", "https://live.kwikmotion.com/alaraby1live/alaraby_abr/playlist.m3u8"),
    "SpacetoonArabic.ae@SD": ("filmtheque-ae", "space-toon-ae", "https://live-uae-next.spacetoongo.com/ST_MENA_NEXT/hls/r9p2hjipmw2kl.m3u8"),
    "CTDecko.cz@SD": ("ct-shared", "decko-art", "http://88.212.15.50/live/ct_art_avc_25p/playlist.m3u8"),
    "Cablenoticias.co@SD": ("formula-co", "cablenoticias", "http://181.78.211.244:8005/play/a09u/index.m3u8"),
    "Bandamax.mx@SD": ("formula-mx", "bandamax", "https://streaming.onnethn.com/Bandamax/index.m3u8"),
    "Wonder.uk@SD": ("filmtheque-uk", "wonder-uk", "https://lds-wonder-rakuten.amagi.tv/playlist.m3u8"),
    "TV5MondeInfo.fr@SD": ("programmecesoir", "tv5-monde-info",
                         "https://tvf-tvfm.otteravision.com/tvf/tvfm/tvfm_720.m3u8"),
    "INTRAVEL.nl@Philippines": ("samsung-gb", "GB400003BM",
                               "https://amg00861-amg00861c10-rakuten-uk-3152.playouts.now.amagi.tv/playlist.m3u8"),
    "RedUno.bo@SantaCruz": ("reduno", "santa-cruz", "http://190.181.61.93:8000/play/a002/index.m3u8"),
    "ELDEBER.bo@SD": ("eldeber", "eldeber-tv", "https://master.tucableip.com/eldebertv/index.m3u8"),
    "OTV.lb@SD": ("streamingtvguides-lb", "OTVLB", "https://otv.hibridcdn.net/otv/tv_abr/playlist.m3u8"),
    "CitytvBogota.co@SD": ("americatvguide-co", "city-tv", "http://138.121.15.230:9002/CITY-TV/index.m3u8"),
    "HGTV.nz@SD": ("sky-nz", "hgtv", "https://mediapackage-sky-hgtv-source.fullscreen.nz/index.m3u8"),
    "Canal13.gt@SD": ("americatvguide-gt", "canal-13", "http://190.61.101.11:7050/play/a07f/index.m3u8"),
    "NHKWorldPremium.jp@SD": ("nhk-world-premium", "nhk-world-premium",
                              "https://media-tyo.hls.nhkworld.jp/hls/wp/live/master.m3u8"),
    "TravelTV.bg@SD": ("traveltv", "travel-tv", "https://streamer103.neterra.tv/travel/live.m3u8"),
    "PBO.pe@SD": ("pbo", "pbo", "https://live-evg11.tv360.bitel.com.pe/bitel/pbo_abr/playlist.m3u8"),
    "13Festival.cl@SD": ("13go", "13festival", "https://origin.dpsgo.com/ssai/event/Nftd0fM2SXasfDlRphvUsg/master.m3u8"),
    "13Realities.cl@SD": ("13go", "13realities", "https://origin.dpsgo.com/ssai/event/g7_JOM0ORki9SR5RKHe-Kw/master.m3u8"),
    "13T.cl@SD": ("13go", "13t", "https://origin.dpsgo.com/ssai/event/f4TrySe8SoiGF8Lu3EIq1g/master.m3u8"),
    "TVUsach.cl@SD": ("tvusach", "stv4", "https://stv4.janus.cl/playlist/stream.m3u8"),
    "TraceLatina.fr@SD": ("programmecesoir", "trace-latina",
                          "https://channels.trace.plus/Traceprod/LATINA_hd/index.m3u8"),
    "AlArabiyaBusiness.ae@SD": ("shahid", "1003218",
                                 "https://live.alarabiya.net/alarabiapublish/aswaaq.smil/playlist.m3u8"),
    "CNAOriginals.sg@SD": ("rakuten-uk", "cna",
                           "https://amg01082-cna-amg01082c1-rlaxx-us-11304.playouts.now.amagi.tv/playlist.m3u8"),
    "NatureTime.ca@SD": ("rakuten-uk", "nature-time-eng-uk-feed",
                         "https://bamusa-naturetime-emea-eng-rakuten.amagi.tv/playlist.m3u8"),
    "TerraMaterWILD.de@German": ("samsung-de", "DEAJ32000012H",
                                "https://53ff3982.wurl.com/master/f36d25e7e52f1ba8d7e56eb859c636563214f541/"
                                "UmxheHhUVi1ldV9UZXJyYU1hdGVyV2lsZF9ITFM/playlist.m3u8"),
    "EstrellaNews.us@SD": ("tubi-estrella", "560215", "https://estrella-news-oando.amagi.tv/playlist.m3u8"),
    "TimesBrasil.br@SD": ("tvplus-br", "cnbc",
                          "https://ssai2-ads.api.leiniao.com/global-adinsertion-api/hls/live/v2/"
                          "21fdb6dd7491406bba86497a24390a25/playlist.m3u8"),
    "Sportitalia.it@SD": ("digitalbitrate", "80",
                         "https://edge-001.streamup.eu/sportitalia/sihd_abr/playlist.m3u8"),
    "Medi1TVArabic.ma@SD": ("medi1tv", "arabic",
                            "https://cdn.live.easybroadcast.io/abr_corp/83_medi1tv-arabic_g90v4ec/playlist.m3u8"),
    "CNNInternational.us@Europe": ("tvpassport", "3012",
                                   "https://d3bp6dwmpbdajl.cloudfront.net/v1/master/3722c60a815c199d9c0ef36c5b73da68a62b09d1/cc-ury0meh5m4nzm/index.m3u8"),
    "Telemundo.us@EastHD": ("tvpassport", "1521", "https://streamer1.nexgen.bz/TELEMUNDO/index.m3u8"),
    "CapeTownTV.za@SD": ("freevision-cape-town", "260", "https://cdn.freevisiontv.co.za/sttv/smil:ctv.stream.smil/playlist.m3u8"),
    "RedeTV.br@SD": ("redetv", "redetv-br", "http://45.162.64.114/REDE_TV/index.m3u8"),
    "2M.ma@SD": ("programmecesoir", "2m-maroc", "http://185.9.2.18/chid_218/index.m3u8"),
    "3HD.th@SD": ("ch3plus", "33HD", "https://live-us1.thaimomo.com/live-as/ch3hd-3/playlist.m3u8"),
    "LawCrime.us@SD": ("tvpassport", "32823", "https://a-cdn.klowdtv.com/live3/law_720p/playlist.m3u8"),
    "ARDalpha.de@SD": ("ard-epg", "alpha", "https://mcdn.br.de/br/fs/ard_alpha/hls/de/master.m3u8"),
    "CNNPortugal.pt@SD": ("meo", "CNNPT",
                          "https://github.com/thomraider12/canaistvpt/raw/main/m3u8s/cnnpt.m3u8"),
    "RUV2.is@SD": ("ruv", "ruv2",
                   "https://ruvlive.akamaized.net/out/v1/2ff7673de40f419fa5164498fae89089/index.m3u8"),
    "ElWatania2.tn@SD": ("tunisiatv", "69c593d18d6cac7a55cd575c",
                        "https://sw1.tanitweb.net/TunisiaTV/_definst_/watania2/playlist.m3u8?DVR"),
    "RudawTV.iq@SD": ("rudaw", "sorani", "https://live.rudaw.net/hls/rudaw-tv/master.m3u8"),
    "QatarTelevision.qa@SD": ("tabie", "23",
                              "https://live.kwikmotion.com/qtv1live/qtv1.smil/playlist.m3u8"),
    "NewsmaxBalkans.ba@SD": ("gledajtv", "newsmax-balkans",
                             "https://newsmaxadria.mts-si.tv/newsmaxadria/index.m3u8"),
    "TRTWorld.tr@SD": ("trt-portal", "129458", "https://mumbai-edge.smartplaytv.in/TRTWorld/index.m3u8"),
    "WildEarth.za@SD": ("wildearth", "452935", "https://dqga3jatxofgx.cloudfront.net/WildEarth.m3u8"),
    "TVEStar.es@HD": ("rtve-star", "11186",
                     "https://rtvelivestream-rtveplayplus.rtve.es/rtvesec/int/star_main_1080.m3u8"),
    "CanalOnce.mx@HD": ("once", "2", "https://vivo.canaloncelive.tv/securepkgr3/oncemexico/playlist.m3u8"),
    "Telemetro.pa@SD": ("telemetro", "telemetro", "http://181.78.121.15:8000/play/a001/index.m3u8"),
    "TVPInfo.pl@SD": ("tvp", "INF", "https://dash4.antik.sk/live/test_tvp_info/playlist.m3u8"),
    "TCTelevision.ec@SD": ("tc", "tc-television", "http://45.224.97.181:9999/TC/index.m3u8"),
    "OromarTV.ec@SD": ("oromar", "Oromartv", "https://stream.oromar.tv/hls/oromartv_hi/index.m3u8"),
    "UTalcaTV.cl@SD": ("utalca", "campustv",
                      "https://unlimited1-us.dps.live/campustv/campustv.smil/campustv/livestream1/chunks.m3u8"),
    "Canal4RD.do@SD": ("rtvd", "canal4rd-1",
                      "https://cdn.protvradiostream.com/canal4rd-1/ngrp:canal4rd-1_all/playlist.m3u8"),
    "CityNewsVancouver.ca@SD": ("citynews-vancouver", "6272887411001",
                                "https://citynewsregional.akamaized.net/hls/live/1024054/Regional_Live_9/master.m3u8"),
    "BBSTV.ug@SD": ("bbs", "ch01", "https://bbstv.ug/hls/ch01/index.m3u8"),
    "MDRFernsehen.de@Thuringen": ("mdr", "thueringen", "https://dash4.antik.sk/live/test_mdr_tizen/playlist.m3u8"),
    "IranInternational.uk@SD": ("iranintl", "iitv",
                               "https://hlspackager.akamaized.net/live/DB/IRAN_INTERNATIONAL/HLS/IRAN_INTERNATIONAL.m3u8"),
    "RCNMas.co@SD": ("rcntotal", "125383", "https://rcntv-rcnmas-1-us.roku.wurl.tv/3000.m3u8"),
    "MovieSphere.us@US": ("lionsgate", "147",
                          "https://amg00353-lionsgatestudio-moviesphere-xumo-zh5u0.amagi.tv/playlist.m3u8"),
    "AsharqNews.sa@SD": ("shahid", "862837", "https://live-news.asharq.com/asharq.m3u8"),
    "AsharqDiscovery.sa@SD": ("asharq-now", "discovery",
                              "https://svs.itworkscdn.net/asharqdiscoverylive/asharqd.smil/playlist_dvr.m3u8"),
    "ChadaTV.ma@SD": ("chada", "tv", "https://edge13.vedge.infomaniak.com/livecast/ik:chadatv/manifest.m3u8"),
    "NusantaraTV.id@SD": ("ntv", "nusantara-tv", "https://nusantaratv.siar.us/nusantaratv/live/playlist.m3u8"),
    "CaracolInternacional.co@SD": ("caracol", "internacional", "https://streaming.onnethn.com/CARACOL_INTERNACIONAL/playlist.m3u8"),
    "AlMaghribia.ma@SD": ("maghribia", "4072", "http://185.9.2.18/chid_205/index.m3u8"),
    "AllegroHD.ar@SD": ("allegro", "spatial-ar", "http://158.172.155.130:8000/play/a0dn/index.m3u8"),
    "EskaTV.pl@SD": ("eska", "624", "http://92.114.85.87:8018/play/a00h/index.m3u8"),
    "StarChannel.gr@SD": ("star-gr", "star4", "http://livestar.siliconweb.com/starvod/star4/star4.m3u8"),
    "ImagenTV.mx@SD": ("imagen-mx", "canal3", "https://igd-it-runtime.otteravision.com/igd/it/it.m3u8"),
    "StingrayClassica.ca@SD": ("samsung-ca", "CA400005W4",
                              "https://lotus.stingray.com/manifest/classica-cla008-montreal/samsungtvplus/master.m3u8"),
    "CBSNews247.us@SD": ("cbs", "CBSN-US",
                        "https://cbsn-us-vtt.cbsnstream.cbsnews.com/out/v1/ef868690d34144509eda696884bf1619/master.m3u8"),
    "CBSNewsMiami.us@SD": ("cbs", "CBSN-MIA",
                          "https://cbsn-mia.cbsnstream.cbsnews.com/out/v1/ac174b7938264d24ae27e56f6584bca0/master.m3u8"),
    "EuropaEuropa.ar@SD": ("europa-europa", "argentina", "https://iptv.bitred.cl/EUROPAEUROPA_HD/index.m3u8"),
    "CineLatino.mx@SD": ("formula-mx", "cinelatino", "http://168.196.127.137:6001/play/a08o/index.m3u8"),
    "TeleHit.mx@SD": ("formula-mx", "telehit", "http://186.148.196.100:8000/play/a01s"),
    "OmanTV.om@SD": ("ayn", "158", "https://partneta.cdn.mgmlcdn.com/omantv/smil:omantv.stream.smil/chunklist.m3u8"),
    "OmanTVCultural.om@SD": ("ayn", "160",
                             "https://partwota.cdn.mgmlcdn.com/omcultural/smil:omcultural.stream.smil/chunklist.m3u8"),
    "OmanTVMubashir.om@SD": ("ayn", "161", "https://partwota.cdn.mgmlcdn.com/omlive/smil:omlive.stream.smil/chunklist.m3u8"),
    "Azteca7.mx@SD": ("formula-mx", "azteca-7", "http://187.245.231.226:8000/play/a0uz/index.m3u8"),
    "DMAX.it@SD": ("dmax-it", "31", "https://streaming.aurora.enhanced.live/live/mpc/"
                                   "enl0eG1mLmVncmVzcy5mZDJ2eHI=/out/v1/IT_SIMULCAST/DMAX/CMAF/index.m3u8"),
    "CricketGold.au@SD": ("plex-au", "697140a85d851f5e69414688-65e0c8c6d899afe817b9d224",
                           "https://streams2.sofast.tv/ptnr-yupptv/title-cricketgold/v1/master/"
                           "611d79b11b77e2f571934fd80ca1413453772ac7/b2048bb8-1686-4432-aa50-647245383e0c/manifest.m3u8"),
    "BloombergTV.us@US": ("plex-au", "697140a85d851f5e69414688-61d73ab0a7b7c7f703418516",
                          "https://bloomberg.com/media-manifest/streams/us.m3u8"),
}
SHAHID_MBC = {
    "MBC1.ae@SD": ("387238", "bitmovin-mbc-1"),
    "MBC4.ae@SD": ("400919", "bitmovin-mbc-4"),
    "MBCMasr.eg@SD": ("387290", "bitmovin-mbc-masr"),
    "MBCMasr2.eg@SD": ("387293", "bitmovin-mbc-masr-2"),
    "MBCIraq.iq@SD": ("387294", "bitmovin-mbc-iraq"),
}

TVPERU_NATIONAL_ROUTES = {
    "http://190.93.224.42/TV-PERU/index.m3u8",
    "http://187.102.210.46/TVPERU/index.m3u8",
}
TVPERU_GUIDE = "https://tvonline.es/programacion-tv/tv-peru"
TVPERU_BROADCAST_SERVICE = "https://tvonline.es/tv-peru-en-directo#broadcast-service"



def binding(entry):
    if (entry["tvgID"] == "Rai1.it@SD" and entry["id"].casefold() == "iptv:rai1.it"
            and any(source["url"] == "https://dash2.antik.sk/live/test_rai_uno_tizen/playlist.m3u8"
                    for source in entry["sources"])):
        return "rai", "rai-1", "Rai 1"
    if entry["tvgID"] in RAI:
        return ("rai", *RAI[entry["tvgID"]])
    if entry["tvgID"] == "24Horas.es@SD":
        return "rtve", "24h", None
    if entry["tvgID"] == TVL_ECUADOR and any(source["url"] == TVL_STREAM for source in entry["sources"]):
        return "tvl", "ecuador", None
    if entry["tvgID"] == "TVSenado.br@SD" and entry["id"].casefold() == "iptv:tvsenado.br":
        return "senado-br", "tv-senado", None
    if entry["tvgID"] == "3sat.de@SD" and entry["id"].casefold() == "iptv:3sat.de":
        return "3sat", "3sat", None
    if entry["tvgID"] == "KTV.kr@SD" and entry["id"].casefold() == "iptv:ktv.kr":
        return "ktv", "ktv", None
    if entry["tvgID"] == "NRK1.no@SD" and entry["id"].casefold() == "iptv:nrk1.no":
        return "nrk", "nrk1", "NRK1"
    if entry["tvgID"] == "APunt.es@SD" and entry["id"].casefold() == "iptv:apunt.es":
        for source in entry["sources"]:
            stream = urlsplit(source["url"])
            if stream.scheme == "https" and stream.hostname == "fastly.live.brightcove.com":
                if stream.path.split("/")[1:4] == ["1846756479408303045", "eu-central-1", APUNT_ACCOUNT]:
                    return "apunt", "internet", None
    if entry["tvgID"] == "ERTNews.gr@HD" and entry["id"].casefold() == "iptv:ertnews.gr":
        if any(urlsplit(source["url"]).hostname == "ert-ucdn.broadpeak-aas.com"
               and "ertnews" in urlsplit(source["url"]).path.casefold() for source in entry["sources"]):
            return "ert", "ertnews", None
    feed = entry["tvgID"]
    if feed == "TVPeru.pe@SD" and entry["id"].casefold() == "iptv:tvperu.pe":
        if any(source["url"] in TVPERU_NATIONAL_ROUTES for source in entry["sources"]):
            return "tvonline", "tv-peru-national", None
    if feed in OFFICIAL_STREAMS and entry["id"].casefold() == "iptv:" + feed.split("@", 1)[0].casefold():
        provider, key, route = OFFICIAL_STREAMS[feed]
        if any(source["url"] == route for source in entry["sources"]):
            return provider, key, None
    if feed in SHAHID_MBC and entry["id"].casefold() == "iptv:" + feed.split("@", 1)[0].casefold():
        key, route = SHAHID_MBC[feed]
        if any(source["url"].startswith(f"https://shd-gcp-live.edgenextcdn.net/live/{route}/")
               for source in entry["sources"]):
            return "shahid", key, None
    if feed in SYDNEY and entry["id"].casefold() == "iptv:" + feed.split("@", 1)[0].casefold():
        route, name = SYDNEY[feed]
        if any(source["url"] == f"https://c.mjh.nz/{route}.m3u8" for source in entry["sources"]):
            return "sydney", f"mjh-{route}", name
    if feed in MEDIASET and entry["id"].casefold() == "iptv:" + feed.split("@", 1)[0].casefold():
        key, name, host, path = MEDIASET[feed]
        for source in entry["sources"]:
            stream = urlsplit(source["url"])
            if stream.scheme == "https" and stream.hostname == host and stream.path.startswith(path):
                return "mediaset", key, name
    if feed in EPGSHARE and entry["id"].casefold() == "iptv:" + feed.split("@", 1)[0].casefold():
        region, key, name, *routes = EPGSHARE[feed]
        if not routes or any(source["url"] in routes for source in entry["sources"]):
            return "epgshare-" + region[:-1].lower(), key, name
    for source in entry["sources"]:
        url = urlsplit(source["url"])
        match = re.fullmatch(r"/plu-([0-9a-f]{24})\.m3u8", url.path)
        if url.hostname == "jmp2.uk" and match:
            return "pluto", match[1], None
    return None


def read_response(url, snapshot, name, maximum=2_500_000, *, user_agent="Zapper guide", headers=None, body=None):
    if snapshot is not None:
        data = (snapshot / name).read_bytes()
    else:
        request_headers = {"User-Agent": user_agent} if user_agent else {}
        request = Request(url, data=body, headers={**request_headers, **(headers or {})})
        with urlopen(request, timeout=12) as response:
            data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("Respuesta de programación demasiado grande")
    return data


def mega_ficcion_rows(now, snapshot):
    data = json.loads(read_response(MEGA_FICCION_GUIDE, snapshot, "pantallazo-megaficcion.json"))
    channel, metadata = data["channel"], data["metadata"]
    if ((channel.get("slug"), channel.get("code"), channel.get("name")) != ("megaficcion", 45, "Mega Ficción")
            or metadata.get("timezone") != "America/Santiago"):
        return
    updated = datetime.fromisoformat(metadata["updated"])
    if updated.tzinfo is None or not now - timedelta(days=3) <= updated <= now + timedelta(minutes=15):
        return
    santiago = ZoneInfo("America/Santiago")
    for event in data["events"]:
        if event.get("channels") != ["megaficcion"]:
            continue
        title = event.get("title", "").strip()
        if not title or title.casefold() in {"mega ficción", "mega ficcion", "sin programación"}:
            continue
        try:
            start = datetime.fromisoformat(event["beginTime"])
            stop = datetime.fromisoformat(event["endTime"])
        except (KeyError, TypeError, ValueError):
            continue
        if any(value.tzinfo is None or value.utcoffset() != value.astimezone(santiago).utcoffset()
               for value in (start, stop)):
            continue
        if start < stop:
            yield start, stop, title, event.get("episodeTitle"), event.get("synopsis"), MEGA_FICCION_GUIDE


def pluto_rows(channel, now, snapshot):
    date_range = urlencode({
        key: (now + timedelta(hours=hours)).isoformat(timespec="seconds").replace("+00:00", "Z")
        for key, hours in (("start", -2), ("stop", 48))
    })
    base = f"https://api.pluto.tv/v2/channels/{channel}"
    data = json.loads(read_response(f"{base}?{date_range}", snapshot, f"epg-pluto-altas/{channel}.json"))
    if data.get("_id") != channel:
        raise ValueError("La grilla de Pluto corresponde a otra señal")
    for item in data["timelines"]:
        start = datetime.fromisoformat(item["start"].replace("Z", "+00:00"))
        stop = datetime.fromisoformat(item["stop"].replace("Z", "+00:00"))
        episode = item.get("episode") or {}
        yield start, stop, item.get("title"), episode.get("name"), episode.get("description"), base


def rai_rows(channel, expected_name, now, snapshot):
    today = now.astimezone(ROME).date()
    for day in (today, today + timedelta(days=1)):
        date = day.strftime("%d-%m-%Y")
        file = f"{channel}-{date}.json"
        url = f"https://www.raiplay.it/palinsesto/app/{channel}/{date}.json"
        try:
            data = json.loads(read_response(url, snapshot, f"epg-emisoras-altas/{file}"))
        except (OSError, ValueError):
            continue
        if data.get("channel") != expected_name or data.get("date") != date:
            continue
        slots = []
        for event in data["events"]:
            if event.get("channel") != expected_name:
                continue
            duration = re.fullmatch(r"(\d+) min", event.get("duration_in_minutes") or "")
            if not duration or not event.get("hour"):
                continue
            try:
                clock = datetime.strptime(event["hour"], "%H:%M").time()
            except ValueError:
                continue
            start = datetime.combine(day, clock, ROME)
            stop = start + timedelta(minutes=int(duration[1]))
            programme = start, stop, event.get("name"), event.get("episode_title"), event.get("description"), url
            slots.append((programme, event.get("path_id"), event.get("playlist_id")))

        # Resolve duplicates of one asset only when the playlist row fits both adjacent slots.
        playlist_slots = [(row, asset) for row, asset, playlist in slots
                          if isinstance(playlist, str) and playlist.isdecimal() and row[0] < row[1]]
        starts = {row[0] for row, _ in playlist_slots}
        stops = {row[1] for row, _ in playlist_slots}
        for programme, asset, playlist in slots:
            if playlist in (None, "") and isinstance(asset, str) and asset.startswith("/video/"):
                start, stop, title = programme[:3]
                matches = [row for row, other_asset in playlist_slots
                           if asset == other_asset and row[2] == title and start < row[1] and row[0] < stop]
                if len(matches) == 1 and matches[0][0] in stops and matches[0][1] in starts:
                    continue
            yield programme


RAI_ASIA_AUSTRALIA_ID = "RaiItalia.it@Australia"
RAI_ASIA_AUSTRALIA_PROVIDER = "raiplay:rai-italia-asia"
RAI_ASIA_AUSTRALIA_GUIDE = "https://www.raiplay.it/palinsesto/raiitalia/Rai-Italia-Asia"


def rai_asia_australia_rows(now, snapshot, conflicts=None):
    """Read the broadcaster's dated international edition with explicit UTC offsets."""
    market = "Rai Italia Asia"
    today = now.astimezone(ROME).date()
    dated_rows = {}
    for offset in (-1, 0, 1, 2):
        date = (today + timedelta(days=offset)).strftime("%d-%m-%Y")
        url = f"{RAI_ASIA_AUSTRALIA_GUIDE}/{date}.json"
        try:
            data = json.loads(read_response(url, snapshot, f"rai-asia-{date}.json"))
        except (OSError, ValueError):
            continue
        if (not isinstance(data, dict) or data.get("channel") != market or data.get("day") != date
                or data.get("absolute_path") != "rai-italia-asia" or not isinstance(data.get("events"), list)):
            continue
        for event in data["events"]:
            if not isinstance(event, dict) or event.get("channel") != market:
                continue
            try:
                start = datetime.strptime(event.get("date") or "", "%Y-%m-%dT%H:%M:%S%z")
                duration = datetime.strptime(event.get("duration") or "", "%H:%M:%S").time()
            except (TypeError, ValueError):
                continue
            stop = start + timedelta(hours=duration.hour, minutes=duration.minute, seconds=duration.second)
            title = event.get("name")
            if start < stop and now < stop and start <= now + timedelta(hours=36) and isinstance(title, str) and title.strip():
                dated_rows.setdefault((start, stop, title), (
                    event.get("episode_title"), event.get("description"), url,
                ))
    for row, detail in sorted(dated_rows.items()):
        start, stop, _ = row
        if any(other != row and start < other[1] and stop > other[0] for other in dated_rows):
            if conflicts is not None:
                conflicts.append((start, stop))
        else:
            yield *row, *detail


def rai_asia_australia_provenance(row):
    return (row.get("zapper-provider-id") == RAI_ASIA_AUSTRALIA_PROVIDER
            and row.get("zapper-provenance", "").startswith(RAI_ASIA_AUSTRALIA_GUIDE + "/"))


def rai_asia_australia_schedule(now, previous, snapshot=None):
    """Keep verified cached slots unless the exact broadcaster now contradicts them."""
    conflicts = []
    fresh = normalized_programmes(RAI_ASIA_AUSTRALIA_ID, RAI_ASIA_AUSTRALIA_PROVIDER,
                                  rai_asia_australia_rows(now, snapshot, conflicts), now)

    def window(row):
        return tuple(datetime.strptime(row.get(key) or "", XMLTV) for key in ("start", "stop"))

    blocked = conflicts + [window(row) for row in fresh]
    cached = []
    for row in previous:
        if row.get("channel") != RAI_ASIA_AUSTRALIA_ID or not rai_asia_australia_provenance(row):
            continue
        try:
            start, stop = window(row)
        except ValueError:
            continue
        if start < stop and now < stop and start <= now + timedelta(hours=36) and all(
            stop <= a or start >= b for a, b in blocked
        ):
            cached.append(row)
    return sorted(fresh + cached, key=window)


def next_flight_strings(page, marker):
    for push in re.finditer(r'self\.__next_f\.push\((\[[^<]*\])\)</script>', page):
        if marker not in push[1]:
            continue
        try:
            payload = json.loads(push[1])
        except ValueError:
            continue
        if len(payload) == 2 and isinstance(payload[1], str):
            yield payload[1]


def rtve_embedded_rows(page, url):
    channel_id = '"uidCanal":"tv-24-horas"'
    decoder = json.JSONDecoder()
    for body in next_flight_strings(page, "tv-24-horas"):
        position = body.find(channel_id)
        if position == -1:
            continue
        start = body.rfind('{"diahoy":', 0, position)
        if start == -1:
            continue
        try:
            channel, _ = decoder.raw_decode(body[start:])
        except ValueError:
            continue
        if channel.get("uidCanal") != "tv-24-horas" or channel.get("idAssetCanal") != "1694255":
            continue
        for item in channel.get("items", []):
            try:
                local = datetime.strptime(item["begintime"], "%Y%m%d%H%M%S").replace(tzinfo=MADRID)
                begin = local.astimezone(timezone.utc)
                end = begin + timedelta(seconds=int(item["duration"]))
            except (KeyError, TypeError, ValueError):
                continue
            title = item.get("original_event_name") or item.get("name")
            subtitle = item.get("episode_name")
            yield begin, end, title, subtitle if subtitle != title else None, item.get("description"), url
        return


def rcntotal_rows(channel, snapshot):
    url = "https://www.rcntotal.com/epg/rcnmas-1446680"
    page = read_response(url, snapshot, "rcnmas.html").decode()
    decoder = json.JSONDecoder()
    for body in next_flight_strings(page, "1446680"):
        for match in re.finditer(r'\{"id":1446680,', body):
            try:
                station, _ = decoder.raw_decode(body[match.start():])
            except ValueError:
                continue
            if station.get("name") != "RCN Más" or not isinstance(station.get("shows"), list):
                continue
            for row in station["shows"]:
                if (not isinstance(row, dict) or row.get("channel_id") != channel
                        or row.get("channel_name") != "RCN Mas"
                        or row.get("is_duplicated") is True):
                    continue
                try:
                    start, stop = (datetime.fromisoformat(row[key].replace("Z", "+00:00"))
                                   for key in ("start_time_in_utc", "end_time_in_utc"))
                except (KeyError, AttributeError, ValueError):
                    continue
                if start.utcoffset() == stop.utcoffset() == timedelta(0):
                    yield start, stop, row.get("name"), None, row.get("description"), url


def rtve_rows(now, snapshot):
    url = "https://www.rtve.es/play/guia-tve/"
    page = read_response(url, snapshot, "epg-emisoras-altas/rtve.html", maximum=6_000_000).decode()
    embedded = list(rtve_embedded_rows(page, url))
    if embedded:
        yield from embedded
        return
    day = now.astimezone(MADRID).date()
    date = f"{day.day} de {MONTHS_ES[day.month - 1]} de {day.year}"
    header = re.search(r'aria-label="Programación de 24 horas de TVE\. [^"]+"', page)
    if not header or date + '"' not in header[0]:
        return
    schedule = re.search(r'id="schedule_24h"[^>]*>(.*?)id="schedule_clan"', page[header.end():], re.S)
    if not schedule:
        return

    def plain(text):
        return unescape(re.sub(r"<[^>]*>", " ", text)).strip()

    for slot in re.finditer(
        r'class="datemi">HOY</span><span class="horemi">(\d\d:\d\d)-(\d\d:\d\d)</span>'
        r'</span><strong><span class="pretitle">(.*?)</span><span class="maintitle">(.*?)</span></strong>'
        r'(?:<p>(.*?)</p>)?', schedule[1], re.S,
    ):
        start, stop = (datetime.combine(day, datetime.strptime(slot[i], "%H:%M").time(), MADRID) for i in (1, 2))
        if stop < start:
            stop += timedelta(days=1)
        title = plain(slot[4])
        subtitle = plain(slot[3])
        yield start, stop, title, subtitle if subtitle != title else None, plain(slot[5] or ""), url


def rtve_star_portal_rows(snapshot):
    url = "https://www.rtve.es/play/tve-internacional/argentina/"
    page = read_response(url, snapshot, "rtve-star-argentina.html", maximum=6_000_000).decode()
    column = re.search(r'<div\b[^>]*\bid="guia-canal-int-sta"[^>]*>(.*?)</section>', page, re.S)
    if not column:
        return
    for tag, card in re.findall(
            r'(<div class="item trigger\b[^>]*>)(.*?)(?=<div class="item trigger\b|\Z)',
            column[1], re.S):
        fields = dict(re.findall(r'\bdata-([a-z-]+)="([^"]*)"', tag))
        try:
            begin, end = (datetime.strptime(fields[key], "%Y-%m-%d %H:%M").replace(tzinfo=MADRID)
                          for key in ("begindate", "enddate"))
            title = unescape(fields["title"])
        except (KeyError, ValueError):
            continue
        if any(moment.utcoffset() != moment.replace(fold=1).utcoffset() for moment in (begin, end)):
            continue
        subtitle = re.search(r'<span\s+class="cat-detalle"[^>]*>(.*?)</span>', card, re.S)
        yield begin, end, title, plain_html(subtitle[1]) if subtitle else None, unescape(fields.get("description", "")), url


def rtve_star_rows(channel, snapshot):
    url = f"https://api.rtve.es/api/lives/broadcasts/{channel}.json?locale=es"
    items = json.loads(read_response(url, snapshot, "rtve-star.json"))["page"]["items"]
    short = []
    for item in items:
        if (item.get("id") != channel or item.get("canal") != "Star HD"
                or item.get("idGolumi") != "rtvesec/int/star_main_"):
            continue
        for key in ("before", "now", "next"):
            slot = item.get(key)
            if not slot:
                continue
            begin, end = (datetime.strptime(slot[field], "%d-%m-%Y %H:%M:%S").replace(tzinfo=MADRID)
                          for field in ("inicio", "fin"))
            short.append((begin, end, slot["titulo"], None, None, url))
    yield from short
    anchors = {(begin, end, title.strip().casefold()) for begin, end, title, *_ in short
               if title.strip().casefold() != "cine"}
    if len(anchors) < 2:
        return
    try:
        long = list(rtve_star_portal_rows(snapshot))
    except (OSError, ValueError):
        return
    if len(anchors & {(begin, end, title.strip().casefold()) for begin, end, title, *_ in long}) >= 2:
        end_of_short = max(row[1] for row in short)
        yield from (row for row in long if row[0] >= end_of_short)


def tvl_rows(now, snapshot):
    url = "https://tvl.asambleanacional.gob.ec/"
    page = read_response(url, snapshot, "epg-emisoras-altas/tvl-ecuador.html").decode()
    today = now.astimezone(QUITO).date()
    date = f"{today.day:02d} de {MONTHS_ES[today.month - 1]} {today.year}"
    header = re.search(r'<p class="header-date">(.*?)</p>', page, re.S)
    if not header or re.sub(r"<[^>]*>", "", header[1]).strip() != f"Quito, {date}":
        return
    if f'<source src="{TVL_STREAM}"' not in page:
        return

    days = ("Lu", "Ma", "Mi", "Ju", "Vi", "Sa", "Do")
    for offset, code in enumerate(days[today.weekday():today.weekday() + 3]):
        schedule = re.search(rf"var dates{code}\s*=\s*(\[.*?\]);", page, re.S)
        if not schedule:
            continue
        day = today + timedelta(days=offset)
        for item in json.loads(schedule[1]):
            times = re.search(r"<p>\s*(\d\d:\d\d)\s*-\s*(\d\d:\d\d)", item["content"])
            if not times or times[1] != item.get("marker"):
                continue
            start, stop = (datetime.combine(day, datetime.strptime(clock, "%H:%M").time(), QUITO)
                           for clock in times.groups())
            title = unescape(item["title"]).strip()
            yield start, stop, title, None, None, url


def senado_br_rows(now, snapshot):
    today = now.astimezone(BRASILIA).date()
    days = {}
    for offset in range(3):
        day = today + timedelta(days=offset)
        date = day.strftime("%d-%m-%Y")
        url = "https://www12.senado.leg.br/tv/programacaoAjax?" + urlencode({"date": date})
        try:
            data = json.loads(read_response(url, snapshot, f"epg-emisoras-altas/tv-senado-br-{date}.json"))
            rows = []
            for item in data:
                start = datetime.combine(day, datetime.strptime(item["horario"], "%H:%M").time(), BRASILIA)
                rows.append((start, item, url))
            if rows and all(left[0] < right[0] for left, right in zip(rows, rows[1:])):
                days[day] = rows
        except (KeyError, OSError, TypeError, ValueError):
            continue

    def plain(value):
        text = unescape(re.sub(r"<[^>]*>", " ", value or "")).strip()
        return None if text in {"", "_", "-"} else text

    for day, rows in days.items():
        next_day = days.get(day + timedelta(days=1), [])
        starts = [row[0] for row in rows[1:]]
        if next_day and next_day[0][0].strftime("%H:%M") == "00:00":
            starts.append(next_day[0][0])
        for (start, item, url), stop in zip(rows, starts):
            yield start, stop, plain(item["programa"]), plain(item["episodio"]), plain(item["sinopse"]), url


def plain_html(value):
    return " ".join(unescape(re.sub(r"<[^>]*>", "", value)).split())


class APuntCarousel(HTMLParser):
    def __init__(self):
        super().__init__()
        self.carousels = []

    def handle_starttag(self, tag, attrs):
        if tag == "ap-schedule-carousel":
            self.carousels.extend(value for key, value in attrs if key == ":cards")


def apunt_live_rows(snapshot):
    page = read_response(APUNT_LIVE, snapshot, "apunt-live.html").decode()
    # The web edition can diverge from terrestrial TV during rights windows.
    if f"players.brightcove.net/{APUNT_ACCOUNT}/" not in page.replace(r"\/", "/"):
        return
    parser = APuntCarousel()
    parser.feed(page)
    if len(parser.carousels) != 1:
        return
    for card in json.loads(parser.carousels[0]):
        slots = []
        for field in ("startTime", "endTime"):
            instant = card[field]
            if instant["timezone_type"] != 1 or not re.fullmatch(r"[+-]\d{2}:\d{2}", instant["timezone"]):
                break
            slots.append(datetime.fromisoformat(instant["date"] + instant["timezone"]))
        if len(slots) == 2:
            yield (*slots, card["title"], card.get("episodeTitle"), card.get("resume"), APUNT_LIVE)


def apunt_web_schedule_rows(day, snapshot):
    url = APUNT_WEB_SCHEDULE + day
    page = read_response(url, snapshot, f"apunt-internet-{day}.html").decode()
    if f'"https://www.apuntmedia.es/programacio/internet/{day}"' not in page.replace(r"\/", "/"):
        return
    for item in re.finditer(
        r'<article\b[^>]*\bdata-start-time="([^"]+)"\s+data-end-time="([^"]+)"[^>]*>(.*?)</article>',
        page, re.S
    ):
        title = re.search(r'<div class="schedule-item__title"[^>]*>(.*?)</div>', item[3], re.S)
        if not title:
            continue
        try:
            start, stop = (datetime.fromisoformat(value) for value in item.group(1, 2))
        except ValueError:
            continue
        if any(value.tzinfo is not None for value in (start, stop)):
            continue
        start, stop = (value.replace(tzinfo=MADRID) for value in (start, stop))
        if any(value.utcoffset() != value.replace(fold=1).utcoffset() for value in (start, stop)):
            continue
        subtitle = re.search(r'<h2 class="schedule-item__episode-title"[^>]*>(.*?)</h2>', item[3], re.S)
        desc = re.search(r'<p class="schedule-item__resume-text"[^>]*>(.*?)</p>', item[3], re.S)
        episode = plain_html(subtitle[1]) if subtitle else None
        description = plain_html(desc[1]) if desc else None
        yield start, stop, plain_html(title[1]), episode, description, url


def apunt_rows(snapshot):
    try:
        live = list(apunt_live_rows(snapshot))
    except (OSError, KeyError, TypeError, ValueError):
        live = []
    yield from live
    last_stop = max((row[1] for row in live), default=None)
    for day in ("hui", "dema"):
        try:
            rows = list(apunt_web_schedule_rows(day, snapshot))
        except (OSError, ValueError):
            continue
        additional = [row for row in rows if last_stop is None or row[0] >= last_stop]
        if additional:
            yield from additional
            last_stop = max(row[1] for row in additional)


def mediaset_rows(channel, expected_name, now, snapshot):
    begin, end = (int((now + timedelta(hours=offset)).timestamp() * 1000) for offset in (-3, 36))
    query = urlencode({"byCallSign": channel, "byListingTime": f"{begin}~{end}"})
    url = f"{MEDIASET_GUIDE}?{query}"
    data = json.loads(read_response(url, snapshot, f"mediaset-gateway-{channel}.json"))
    if data.get("isOk") is not True or data.get("service") != "allListingFeedEpg":
        return
    for guide in data["response"]["entries"]:
        if guide.get("guid") != channel:
            continue
        station = list(guide["stations"].values())
        if len(station) != 1 or (station[0].get("callSign"), station[0].get("title")) != (channel, expected_name):
            continue
        for row in guide["listings"]:
            start, stop = (datetime.fromtimestamp(row[key] / 1000, tz=timezone.utc)
                           for key in ("startTime", "endTime"))
            yield (start, stop, row.get("mediasetlisting$epgTitle"),
                   row.get("mediasetlisting$shortDescription"), row.get("description"), MEDIASET_GUIDE)


def once_rows(channel, snapshot):
    data = json.loads(read_response(ONCE_GUIDE, snapshot, "once.json"))
    for row in data:
        if str(row.get("n_tipo_senal")) != channel:
            continue
        try:
            start, stop = (datetime.fromisoformat(f"{row['fecha']}T{row[key]}").replace(tzinfo=MEXICO_CITY)
                           for key in ("h_inicio", "h_termino"))
        except (KeyError, ValueError):
            continue
        if stop < start:
            stop += timedelta(days=1)
        yield start, stop, row.get("barra"), row.get("titulo"), row.get("sinopsis"), ONCE_GUIDE


def telemetro_rows(now, snapshot):
    page = read_response(TELEMETRO_GUIDE, snapshot, "telemetro.html").decode()
    generated = re.search(r"<!-- Request Time: (\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2} -->", page)
    if not generated:
        return
    first_day = datetime.fromisoformat(generated[1]).date()
    if not timedelta(0) <= now.astimezone(PANAMA).date() - first_day <= timedelta(days=1):
        return
    sections = re.split(r'<label for="id-[0-2]" class="programming-grid__grid-selector-btn">(Hoy|Mañana|Pasado)</label>', page)
    starts = []
    for label, section in zip(sections[1::2], sections[2::2]):
        day = first_day + timedelta(days=("Hoy", "Mañana", "Pasado").index(label))
        slots = []
        for article in re.findall(r'<article class="programming-grid__show-info-wrapper[^>]*>(.*?)</article>', section, re.S):
            clock = re.search(r'\b(\d{2}:\d{2})\s*<small[^>]*>(AM|PM)</small>', article)
            title = re.search(r'<h2 class="programming-grid__show-name">(.*?)</h2>', article, re.S)
            if clock and title:
                start = datetime.combine(day, datetime.strptime(f"{clock[1]} {clock[2]}", "%I:%M %p").time(), PANAMA)
                slots.append((start, plain_html(title[1])))
        if all(left[0] < right[0] for left, right in zip(slots, slots[1:])):
            starts.extend(slots)
    for (start, title), (stop, _) in zip(starts, starts[1:]):
        if start < stop and stop.date() <= start.date() + timedelta(days=1):
            yield start, stop, title, None, None, TELEMETRO_GUIDE


def tvp_rows(channel, now, snapshot):
    today = now.astimezone(WARSAW).date()
    for offset in (-1, 0, 1):
        day = today + timedelta(days=offset)
        url = f"{TVP_INFO_GUIDE}?date={day}"
        try:
            page = read_response(url, snapshot, f"tvp-info-{day}.html").decode()
        except (OSError, ValueError):
            continue
        raw = re.search(r'window\.__stationsProgram\[0\]\s*=\s*(\{.*?)\s*;\s*</script>', page, re.S)
        if not raw:
            continue
        guide = json.loads(raw[1])
        station = guide["station"]
        if (station.get("code"), station.get("name"), station.get("regional")) != (channel, "TVP Info", False):
            continue
        for row in guide["items"]:
            if row.get("station_code") == channel and row.get("date_program") == day.isoformat():
                start, stop = (datetime.fromtimestamp(row[key] / 1000, tz=timezone.utc)
                               for key in ("date_start", "date_end"))
                description = (row.get("program") or {}).get("description")
                yield start, stop, row.get("title"), None, description, url


def tc_rows(now, snapshot):
    data = read_response(TC_TABLE, snapshot, "tc-csv.csv").decode("utf-8-sig")
    table = list(csv.reader(data.splitlines()))
    if len(table) < 5:
        return
    today = now.astimezone(QUITO).date()
    days = {}
    for index, date_text in enumerate(table[2]):
        if not date_text or table[3][index:index + 3] != ["id", "HORA", "PROGRAMA"]:
            continue
        day = datetime.strptime(date_text, "%d/%m/%Y").date()
        if abs((today - day).days) > 3:
            continue
        slots = []
        for row in table[4:]:
            if len(row) <= index + 2 or not (row[index + 1] and row[index + 2]):
                continue
            clock = datetime.strptime(row[index + 1], "%H:%M:%S").time()
            slots.append((datetime.combine(day, clock, QUITO), row[index + 2]))
        days[day] = slots
    for day, slots in days.items():
        following = days.get(day + timedelta(days=1), [])
        conflicts = [right[0] for left, right in zip(slots, slots[1:]) if right[0] <= left[0]]
        cutoff = min(conflicts) if conflicts else None
        for (start, title), (stop, _) in zip(slots, slots[1:] + following[:1]):
            if start < stop and (cutoff is None or stop <= cutoff):
                yield start, stop, title, None, None, TC_GUIDE


def shahid_rows(channel, now, snapshot):
    for offset in (0, 1):
        day = now.date() + timedelta(days=offset)
        query = urlencode({"csvChannelIds": channel, "from": f"{day}T00:00:00.000Z",
                           "to": f"{day}T23:59:59.999Z", "country": "SA",
                           "language": "en", "Accept-Language": "en"})
        url = f"{SHAHID_GUIDE}?{query}"
        try:
            data = json.loads(read_response(url, snapshot, f"shahid-{channel}-{day}.json"))
        except (OSError, ValueError):
            continue
        for group in data["items"]:
            if str(group.get("channelId")) != channel:
                continue
            for row in group["items"]:
                title = row.get("title")
                if row.get("emptySlot") or not title or title.casefold() == "tv guide is not available":
                    continue
                start, stop = (datetime.fromisoformat(row[key]).replace(microsecond=0)
                               for key in ("actualFrom", "actualTo"))
                if start.tzinfo is not None and stop.tzinfo is not None:
                    yield start, stop, title, None, row.get("description"), url


def asharq_now_rows(channel, now, snapshot):
    def rows(stamp, filename):
        url = ASHARQ_NOW_GUIDE.format(channel, stamp)
        try:
            schedule = json.loads(read_response(url, snapshot, filename))
        except (OSError, ValueError):
            return
        if schedule.get("status") is not True:
            return
        for entry in schedule.get("data", []):
            try:
                start, stop = (datetime.fromisoformat(entry[field].replace("Z", "+00:00"))
                               for field in ("start", "stop"))
                name = entry["name"]
                title = name.get("ar") or name.get("en")
                description = entry.get("description") or {}
                details = description.get("ar") or description.get("en")
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if start.tzinfo is not None and stop.tzinfo is not None and start < stop and title:
                yield start, stop, title, None, details, url

    today = now.astimezone(timezone.utc).date()
    stamp = now.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    live = next((row for row in rows(stamp, f"asharq-now-{channel}-current-{today}.json")
                 if row[0] <= now < row[1]), None)
    if live:
        yield live
    for offset in (-1, 0, 1, 2):
        day = today + timedelta(days=offset)
        for row in rows(f"{day}T23:59:59.000Z", f"asharq-now-{channel}-end-{day}.json"):
            # The current endpoint has precedence over any conflicting forward listing.
            if live and row[0] < live[1] and live[0] < row[1]:
                continue
            yield row


def chada_rows(now, snapshot):
    page = read_response(CHADA_GUIDE, snapshot, "chada.html").decode()
    if 'data-stream="https://chadatv.vedge.infomaniak.com/livecast/ik:chadatv/manifest.m3u8"' not in page:
        return
    generated = re.search(r'<span>(\d{2}/\d{2}/\d{4})\s*•\s*\d{2}:\d{2}\s*\(GMT\)</span>', page)
    if not generated or datetime.strptime(generated[1], "%d/%m/%Y").date() != now.date():
        return
    sections = re.split(r'data-day-panel="([1-7])"', page)
    for weekday, section in zip(sections[1::2], sections[2::2]):
        day = now.date() + timedelta(days=(int(weekday) - now.isoweekday()) % 7)
        if (day - now.date()).days > 2:
            continue
        for article in re.findall(r'<article class="chada-epg-row[^>]*>(.*?)</article>', section, re.S):
            start = re.search(r'<span class="chada-epg-time__start">(\d{2}:\d{2})</span>', article)
            stop = re.search(r'<span class="chada-epg-time__end">(\d{2}:\d{2})</span>', article)
            title = re.search(r'<h4 class="chada-epg-card__title">(.*?)</h4>', article, re.S)
            description = re.search(r'<p class="chada-epg-card__desc">(.*?)</p>', article, re.S)
            if not (start and stop and title):
                continue
            midnight = datetime.combine(day, datetime.min.time(), timezone.utc)
            start_hour, start_minute = map(int, start[1].split(":"))
            stop_hour, stop_minute = map(int, stop[1].split(":"))
            if not (start_hour <= 23 and start_minute <= 59 and
                    (stop_hour <= 23 and stop_minute <= 59 or stop[1] == "24:00")):
                continue
            begin = midnight + timedelta(hours=start_hour, minutes=start_minute)
            end = midnight + timedelta(hours=stop_hour, minutes=stop_minute)
            if end < begin:
                end += timedelta(days=1)
            yield begin, end, plain_html(title[1]), None, plain_html(description[1]) if description else None, CHADA_GUIDE


def ntv_rows(now, snapshot):
    today = (now.astimezone(JAKARTA) - timedelta(hours=4)).date()
    for offset in (-1, 0, 1):
        day = today + timedelta(days=offset)
        label = f"{WEEKDAYS_ID[day.weekday()]} {day.day} {MONTHS_ID[day.month - 1]} {day.year}"
        url = f"{NTV_GUIDE}-{label.casefold().replace(' ', '-')}"
        try:
            page = read_response(url, snapshot, f"ntv-{day}.html").decode()
        except (OSError, ValueError):
            continue
        heading = re.search(r'<h1[^>]*>(.*?)</h1>', page, re.S)
        schedule = re.search(r'<p>[^<]*04\.00-\d{2}\.\d{2}.*?</p>', page, re.S)
        if not heading or plain_html(heading[1]) != f"Jadwal Program Nusantara TV {label}" or not schedule:
            continue
        midnight = datetime.combine(day, datetime.min.time(), JAKARTA)
        for item in re.split(r'<br\s*/?>', schedule[0]):
            row = re.fullmatch(r'(\d{2}\.\d{2})\s*-\s*(\d{2}\.\d{2})\s+(.+)', plain_html(item))
            if not row:
                continue
            start, stop = (datetime.strptime(row[index], "%H.%M").time() for index in (1, 2))
            begin, end = (midnight + timedelta(days=(clock.hour < 4), hours=clock.hour, minutes=clock.minute)
                          for clock in (start, stop))
            if end <= begin:
                end += timedelta(days=1)
            yield begin, end, row[3], None, None, url


SKY_NZ_GUIDE = "https://api.skyone.co.nz/exp/graph"
SKY_NZ_LIFESTYLE_GROUP = "LOXeZgvmRZ6T0b9geXwgy"
SKY_NZ_QUERY = """query getChannelGroup($id: ID!, $date: LocalDate) {
    experience(appId: TV_GUIDE_WEB) {
        channelGroup(id: $id) {
            id
            channels {
                ... on LinearChannel {
                    id title number
                    slotsForDay(date: $date) {
                        slots {
                            startMs endMs
                            programme {
                                ... on Episode { title synopsis show { title } }
                                ... on Movie { title synopsis }
                                ... on PayPerViewEventProgram { title }
                            }
                        }
                    }
                }
            }
        }
    }
}"""


def sky_nz_hgtv_rows(now, snapshot):
    local = ZoneInfo("Pacific/Auckland")
    today = now.astimezone(local).date()
    for offset in range(3):
        day = today + timedelta(days=offset)
        filename = f"hgtv-sky-{day}.json"
        try:
            if snapshot is None:
                body = json.dumps({"query": SKY_NZ_QUERY,
                                   "variables": {"id": SKY_NZ_LIFESTYLE_GROUP, "date": str(day)}}).encode()
                data = json.loads(read_response(SKY_NZ_GUIDE, None, filename,
                                                headers={"Content-Type": "application/json"}, body=body))
                group = data["data"]["experience"]["channelGroup"]
                if group["id"] != SKY_NZ_LIFESTYLE_GROUP:
                    continue
                channel = next(row for row in group["channels"] if row.get("id") == "0130")
            else:
                channel = json.loads(read_response(SKY_NZ_GUIDE, snapshot, filename))
            if (channel.get("id"), channel.get("title"), channel.get("number")) != ("0130", "HGTV", 21):
                continue
            slots = channel["slotsForDay"]["slots"]
        except (OSError, KeyError, StopIteration, TypeError, ValueError):
            continue
        for slot in slots:
            programme = slot.get("programme") or {}
            episode = programme.get("title")
            if not isinstance(episode, str) or episode.strip().casefold() in {"", "tba", "no data"}:
                continue
            try:
                start, stop = (datetime.fromtimestamp(slot[key] / 1000, timezone.utc) for key in ("startMs", "endMs"))
            except (KeyError, OSError, OverflowError, TypeError, ValueError):
                continue
            if abs((start.astimezone(local).date() - day).days) > 1:
                continue
            series = (programme.get("show") or {}).get("title")
            yield start, stop, series or episode, episode if series != episode and series else None, programme.get("synopsis"), SKY_NZ_GUIDE


class ProgrammeJSONLD(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.documents = []

    def handle_starttag(self, tag, attrs):
        self.inside = tag == "script" and dict(attrs).get("type") == "application/ld+json"

    def handle_endtag(self, tag):
        if tag == "script":
            self.inside = False

    def handle_data(self, data):
        if self.inside:
            try:
                self.documents.append(json.loads(data))
            except ValueError:
                pass


def otv_lebanon_rows(snapshot):
    parser = ProgrammeJSONLD()
    parser.feed(read_response(OTV_LEBANON_GUIDE, snapshot, "otv-lebanon.html").decode())
    for document in parser.documents:
        if not isinstance(document, dict):
            continue
        listing = document.get("mainEntity")
        if not isinstance(listing, dict) or listing.get("@type") != "ItemList":
            continue
        entries = listing.get("itemListElement")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            item = entry.get("item") if isinstance(entry, dict) else None
            if not isinstance(item, dict) or item.get("@type") != "BroadcastEvent":
                continue
            station = item.get("publishedOn")
            if (not isinstance(station, dict)
                    or (station.get("@type"), station.get("identifier"), station.get("name"))
                    != ("TelevisionChannel", "OTVLB", "OTV")):
                continue
            try:
                start, stop = (datetime.fromisoformat(item[field].replace("Z", "+00:00"))
                               for field in ("startDate", "endDate"))
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if start.tzinfo is not None and stop.tzinfo is not None:
                yield start, stop, item.get("name"), None, item.get("description"), OTV_LEBANON_GUIDE


def tvperu_national_rows(snapshot):
    parser = ProgrammeJSONLD()
    parser.feed(read_response(TVPERU_GUIDE, snapshot, "tvonline-tvperu.html").decode())
    events = list(parser.documents)
    while events:
        item = events.pop()
        if isinstance(item, list):
            events.extend(item)
        elif isinstance(item, dict):
            events.extend(item.values())
            if item.get("@type") != "BroadcastEvent":
                continue
            station = item.get("publishedOn")
            if not isinstance(station, dict) or station.get("@id") != TVPERU_BROADCAST_SERVICE:
                continue
            title = item.get("name")
            if not isinstance(title, str) or title.strip().casefold() in {"", "no data", "sin información", "sin programación"}:
                continue
            try:
                begin, end = (datetime.fromisoformat(item[key].replace("Z", "+00:00")) for key in ("startDate", "endDate"))
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if begin.tzinfo is not None and end.tzinfo is not None:
                work = item.get("workPerformed")
                description = work.get("description") if isinstance(work, dict) else None
                yield begin, end, title, None, description, TVPERU_GUIDE


def nhk_world_premium_rows(now, snapshot):
    url = "https://nhkworldpremium.com/backend/api/v1/front/episodes?lang=en"
    calendar = json.loads(read_response(url, snapshot, "nhk-world-premium-en.json"))["item"]
    tokyo = ZoneInfo("Asia/Tokyo")
    today = now.astimezone(tokyo).date()
    start_date, end_date = (datetime.strptime(calendar[key], "%Y-%m-%d").date()
                            for key in ("startDate", "endDate"))
    if not start_date - timedelta(days=1) <= today <= end_date <= start_date + timedelta(days=8):
        return
    for episode in calendar.get("episodes", []):
        title = episode.get("programTitle")
        if (not isinstance(title, str) or title.strip().casefold() in {"", "tba", "mini program"}
                or episode.get("suspendFlg") is not False or not episode.get("episodeId")):
            continue
        try:
            begin = datetime.strptime(episode["schedule"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=tokyo)
            match = re.fullmatch(r"(\d{2}):([0-5]\d):([0-5]\d)", episode["period"])
        except (KeyError, TypeError, ValueError):
            continue
        if match is None or not start_date - timedelta(days=1) <= begin.date() <= end_date:
            continue
        duration = timedelta(hours=int(match[1]), minutes=int(match[2]), seconds=int(match[3]))
        if timedelta(0) < duration <= timedelta(hours=12):
            yield begin, begin + duration, title, episode.get("episodeTitle"), None, url


def caracol_rows(now, snapshot):
    page = read_response(CARACOL_GUIDE, snapshot, "caracol.html").decode()
    if '<meta property="mrf:sections" content="Programación Internacional">' not in page:
        return
    today = now.astimezone(BOGOTA).date()
    if f'<div class="ScheduleDay today" data-day="{today:%A}"' not in page:
        return
    sections = re.split(r'<div class="ScheduleDay" data-day="([^"]+)"', page)
    for label, section in zip(sections[1::2], sections[2::2]):
        if label not in WEEKDAYS_ES:
            continue
        day = today + timedelta(days=(WEEKDAYS_ES.index(label) - today.weekday()) % 7)
        if (day - today).days > 2:
            continue
        for item in re.split(r'<div class="ScheduleDay-Content-item\b', section)[1:]:
            title = re.search(r'<h2 class="ScheduleDay-info-title">(.*?)</h2>', item, re.S)
            clock = re.search(r'<div class="ScheduleDay-time-content flex">\s*<span>(\d{1,2}:\d{2}[AP]M)</span>'
                              r'(?:\s|&nbsp;)*-(?:\s|&nbsp;)*<span>(\d{1,2}:\d{2}[AP]M)</span>', item)
            if not (title and clock):
                continue
            start, stop = (datetime.strptime(re.sub(r'^00:', '12:', stamp), "%I:%M%p").time()
                           for stamp in clock.groups())
            begin, end = (datetime.combine(day, value, BOGOTA) for value in (start, stop))
            if end < begin:
                end += timedelta(days=1)
            yield begin, end, plain_html(title[1]), None, None, CARACOL_GUIDE


def oromar_rows(channel, now, snapshot):
    url = "https://oromartv.com/api/schedule"
    station = json.loads(read_response(url, snapshot, "oromar-schedule.json"))[channel]
    if (station["title"] != channel
            or station["streamUrl"] != "https://stream.oromar.tv/hls/oromartv.m3u8"):
        return
    today = now.astimezone(QUITO).date()
    for offset in range(-1, 3):
        day = today + timedelta(days=offset)
        for item in station["programas"]:
            if WEEKDAYS_ES[day.weekday()] not in item["dias"]:
                continue
            start, stop = (datetime.strptime(item[key], "%H:%M:%S").time() for key in ("start", "end"))
            if start == stop:
                continue
            begin = datetime.combine(day, start, QUITO)
            end = datetime.combine(day + timedelta(days=(stop < start)), stop, QUITO)
            yield begin, end, item["name"], None, item.get("descripcion"), url


def utalca_rows(now, snapshot):
    url = "https://tv.utalca.cl/programacion-campustv/"
    page = read_response(url, snapshot, "utalca-schedule.html").decode()
    headings = [plain_html(value) for value in re.findall(r"<h2\b[^>]*>(.*?)</h2>", page, re.S)]
    if not any(value.startswith("PROGRAMACIÓN UTALCATV") for value in headings):
        return
    weekdays = [value.upper() for value in WEEKDAYS_ES]
    sections = {value: i for i, value in enumerate(headings) if value in weekdays}
    today = now.astimezone(SANTIAGO).date()
    for offset in range(-1, 3):
        day = today + timedelta(days=offset)
        index = sections.get(weekdays[day.weekday()])
        if index is None:
            continue
        limit = min((i for i in sections.values() if i > index), default=len(headings))
        by_start = defaultdict(set)
        for i in range(index + 1, limit - 1):
            value, title = headings[i:i + 2]
            if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
                if re.fullmatch(r"\d\d:\d\d", title):
                    title = ""
                by_start[value].add(title)
        starts = sorted(by_start)
        for start, stop in zip(starts, starts[1:]):
            titles = by_start[start]
            if len(titles) != 1:
                continue
            begin, end = (datetime.combine(day, datetime.strptime(value, "%H:%M").time(), SANTIAGO)
                          for value in (start, stop))
            yield begin, end, next(iter(titles)), None, None, url


def rtvd_rows(now, snapshot):
    url = "https://rtvd.gob.do/portada/"
    page = read_response(url, snapshot, "rtvd-schedule.html").decode()
    if "canal4rd-1/ngrp:canal4rd-1_all/playlist.m3u8" not in page:
        return
    today = now.astimezone(SANTO_DOMINGO).date()
    for row in re.finditer(
        r'<li class="item" data-start-time="([^"]*)" data-end-time="([^"]*)"\s+'
        r'data-visible-days="([0-6,]*)">(?:(?!</li>).)*?<h3>(.*?)</h3>', page, re.S,
    ):
        try:
            start, stop = (datetime.strptime(value, "%H:%M:%S").time() for value in row.group(1, 2))
        except ValueError:
            continue
        for offset in range(-1, 3):
            day = today + timedelta(days=offset)
            if str(day.isoweekday() % 7) not in row[3].split(","):
                continue
            begin = datetime.combine(day, start, SANTO_DOMINGO)
            end = datetime.combine(day + timedelta(days=(stop < start)), stop, SANTO_DOMINGO)
            yield begin, end + timedelta(seconds=1), plain_html(row[4]), None, None, url


def citynews_vancouver_rows(channel, snapshot):
    url = "https://vancouver.citynews.ca/video/"
    page = read_response(url, snapshot, "citynews-vancouver.html", maximum=5_000_000).decode()
    if (f'data-video-id="{channel}"' not in page
            or 'data-channel="citynews_bc" data-timezone="America/Vancouver"' not in page):
        return
    for tile in re.split(r'<div[^>]+class="cn-schedule-tile\s[^"]*"\s+data-start=', page)[1:]:
        dates = re.match(r'"([^"]+)"\s+data-end="([^"]+)"', tile)
        title = re.search(r'<div class="cn-schedule-popup-title">(.*?)</div>', tile, re.S)
        if not dates or not title:
            continue
        begin, end = (datetime.fromisoformat(value) for value in dates.groups())
        if begin.tzinfo is not None and end.tzinfo is not None:
            yield begin, end, plain_html(title[1]), None, None, url


def bbs_rows(channel, now, snapshot):
    url = "https://bbstv.ug/live/"
    page = read_response(url, snapshot, "bbs-live.html").decode()
    if f'<source src="https://bbstv.ug/hls/{channel}/index.m3u8"' not in page:
        return
    today = now.astimezone(KAMPALA).date()
    weekdays = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    panels = dict(re.findall(r'<div class="epg-day-panel[^"]*"\s+data-day="([A-Z][a-z]{2})"[^>]*>'
                             r'(.*?)</table>', page, re.S))
    for offset in range(-1, 3):
        day = today + timedelta(days=offset)
        for row in re.finditer(r'<td class="epg-time">\s*(\d{2}:\d{2})\s*[-–]\s*(\d{2}:\d{2})\s*</td>'
                               r'\s*<td class="epg-title">(.*?)</td>', panels.get(weekdays[day.weekday()], ""), re.S):
            try:
                start, stop = (datetime.strptime(value, "%H:%M").time() for value in row.group(1, 2))
            except ValueError:
                continue
            begin = datetime.combine(day, start, KAMPALA)
            end = datetime.combine(day + timedelta(days=(stop < start)), stop, KAMPALA)
            yield begin, end, plain_html(row[3]), None, None, url


def mdr_rows(now, snapshot):
    today = now.astimezone(BERLIN).date()
    for offset in (-1, 0, 1):
        day = today + timedelta(days=offset)
        url = f"https://www.mdr.de/tv/programm?date={day}"
        try:
            page = read_response(url, snapshot, f"mdr-{day}.html").decode()
        except OSError:
            continue
        heading = re.search(r'<h1 class="viewdate epsilon">(.*?)</h1>', page, re.S)
        if not heading or not plain_html(heading[1]).endswith(day.strftime("%d.%m.%Y")):
            continue
        previous = None
        broadcast_day = day
        for section in re.split(r'<li class="program theta[^"]*">', page)[1:]:
            section = section.split("</li>", 1)[0]
            clock = re.search(r'<span class="time"\s*>\s*(\d{2}:\d{2})\s*</span>\s*'
                              r'<span class="until">bis\s+(\d{2}:\d{2})\s*</span>', section)
            if not clock:
                continue
            start, stop = (datetime.strptime(value, "%H:%M").time() for value in clock.groups())
            if previous is not None and start < previous:
                if broadcast_day != day:
                    break
                broadcast_day += timedelta(days=1)
            previous = start
            titles = [plain_html(value) for value in re.findall(r'<h3 class="theta">(.*?)</h3>', section, re.S)]
            if len(titles) != 1:
                titles = [value for value in titles if value.startswith("MDR THÜRINGEN JOURNAL")]
            if len(titles) == 1:
                begin = datetime.combine(broadcast_day, start, BERLIN)
                end = datetime.combine(broadcast_day + timedelta(days=(stop < start)), stop, BERLIN)
                yield begin, end, titles[0], None, None, url


def iranintl_rows(channel, snapshot):
    url = "https://www.iranintl.com/en/tvschedule"
    page = read_response(url, snapshot, "iranintl-schedule.html").decode()
    decoder = json.JSONDecoder()
    for body in next_flight_strings(page, "scheduleData"):
        match = re.search(r'"scheduleData"\s*:\s*', body)
        if not match:
            continue
        days, _ = decoder.raw_decode(body[match.end():])
        for day in days:
            for slot in day["items"]:
                programme = slot.get("programme") or {}
                if (not slot.get("id", "").startswith(f"btsschedule.{channel}.")
                        or not programme.get("id", "").startswith(f"programme.{channel}.")
                        or programme.get("language") != "farsi"):
                    continue
                duration = re.fullmatch(r"(\d{2}):([0-5]\d):([0-5]\d):00", slot.get("duration", ""))
                if not duration:
                    continue
                begin = datetime.fromisoformat(slot["broadcastTime"].replace("Z", "+00:00"))
                if begin.utcoffset() != timedelta(0):
                    continue
                hours, minutes, seconds = map(int, duration.groups())
                end = begin + timedelta(hours=hours, minutes=minutes, seconds=seconds)
                yield begin, end, programme.get("title"), None, None, url


def maghribia_rows(now, snapshot):
    page = read_response(MAGHRIBIA_GUIDE, snapshot, "magh.html").decode()
    if '<title>شبكة برامج قناة المغربية | SNRT' not in page:
        return
    today = (now.astimezone(CASABLANCA) - timedelta(hours=7)).date()
    sections = re.split(r'<div class="[^\"]*\bgrille-line (\d{8})\b[^\"]*"', page)
    days = defaultdict(list)
    for day, section in zip(sections[1::2], sections[2::2]):
        broadcast_date = datetime.strptime(day, "%Y%m%d").date()
        if not -1 <= (broadcast_date - today).days <= 3:
            continue
        clock = re.search(r'<div class="[^\"]*\bgrille-time\b[^\"]*">(.*?)</div>', section, re.S)
        title = re.search(r'<h2 class="program-title-sm">(.*?)</h2>', section, re.S)
        if not clock:
            continue
        stamp = re.search(r'\b(\d{2})H(\d{2})\b', plain_html(clock[1]))
        if not stamp:
            continue
        hour, minute = map(int, stamp.groups())
        midnight = datetime.combine(broadcast_date, datetime.min.time(), CASABLANCA)
        start = midnight + timedelta(days=(hour < 7), hours=hour, minutes=minute)
        days[broadcast_date].append((start, plain_html(title[1]) if title else ""))
    for day, slots in days.items():
        if not all(left[0] < right[0] for left, right in zip(slots, slots[1:])):
            continue
        for (start, title), (stop, _) in zip(slots, slots[1:] + days.get(day + timedelta(days=1), [])[:1]):
            yield start, stop, title, None, None, MAGHRIBIA_GUIDE


def allegro_rows(now, snapshot):
    data = json.loads(read_response(ALLEGRO_GUIDE, snapshot, "allegro-data.json"))
    today = (now.astimezone(BUENOS_AIRES) - timedelta(hours=7)).date()
    days = {}
    for offset in (-1, 0, 1, 2, 3):
        day = today + timedelta(days=offset)
        midnight = datetime.combine(day, datetime.min.time(), BUENOS_AIRES)
        slots = []
        for row in data["dias"].get(day.isoformat(), []):
            clock = datetime.strptime(row["h"], "%H:%M").time()
            start = midnight + timedelta(days=(clock.hour < 7), hours=clock.hour, minutes=clock.minute)
            slots.append((start, row["t"]))
        if slots and all(left[0] < right[0] for left, right in zip(slots, slots[1:])):
            days[day] = slots
    for day, slots in days.items():
        for (start, title), (stop, _) in zip(slots, slots[1:] + days.get(day + timedelta(days=1), [])[:1]):
            details = data["titulos"].get(title, {})
            yield start, stop, title, None, details.get("s"), ALLEGRO_GUIDE


def eska_rows(snapshot):
    for page in (1, 2):
        url = ESKA_GUIDE.format(page)
        try:
            document = read_response(url, snapshot, f"eska-tv-html_module_page{page}_").decode()
        except (OSError, ValueError):
            continue
        blocks = re.split(r'<div class="tv__row[^"]*" data-channel="([^"]+)">', document)
        for name, block in zip(blocks[1::2], blocks[2::2]):
            if name != "Eska TV":
                continue
            for start, stop, title in re.findall(
                r'data-start="(\d{13})" data-end="(\d{13})"[^>]*>'
                r'<span class="tvcast__title" title="([^"]*)"', block
            ):
                begin, end = (datetime.fromtimestamp(int(stamp) / 1000, timezone.utc)
                              for stamp in (start, stop))
                yield begin, end, plain_html(title), None, None, url


def star_greece_rows(now, snapshot):
    today = (now.astimezone(ATHENS) - timedelta(hours=6)).date()
    days = {}
    for offset in (0, 1, 2):
        day = today + timedelta(days=offset)
        url = STAR_GREECE_GUIDE + "?" + urlencode({"dateRequest": f"{day}T06:00:00"})
        try:
            page = read_response(url, snapshot, f"star-{day}.html").decode()
        except (OSError, ValueError):
            continue
        slots = []
        for row in page.split('<div class="schedule-rows__top">')[1:]:
            clock = re.search(r'<div class="time">\s*(\d{2}:\d{2})', row)
            title = re.search(r'<div class="content">\s*<h2>(.*?)</h2>', row, re.S)
            if clock and title:
                time = datetime.strptime(clock[1], "%H:%M").time()
                broadcast_date = day + timedelta(days=(time.hour < 6))
                slots.append((datetime.combine(broadcast_date, time, ATHENS), plain_html(title[1]), url))
        if slots and all(left[0] < right[0] for left, right in zip(slots, slots[1:])):
            days[day] = slots
    for day, slots in days.items():
        for (start, title, url), (stop, _, _) in zip(slots, slots[1:] + days.get(day + timedelta(days=1), [])[:1]):
            yield start, stop, title, None, None, url


def formula_rows(station, snapshot, market="mx"):
    base = FORMULA_GUIDE.format(market=market) + station + "/"
    for path in ("", "manana/"):
        url = base + path
        filename = f"formula-{station}-{path.rstrip('/') or 'hoy'}.html"
        try:
            page = read_response(url, snapshot, filename).decode()
            schema = re.search(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S)
            nodes = json.loads(schema[1])["@graph"] if schema else []
        except (OSError, ValueError):
            continue
        movies = dict(re.findall(r'<li class="pmx-it\b[^"]*" id="(p\d+)"[^>]*>'
                                 r'(?:(?!</li>).)*?<p class="pmx-it__ep">(.*?)</p>', page, re.S))
        for node in nodes:
            if node.get("@id") != url + "#programacion" or node.get("@type") != "ItemList":
                continue
            for entry in node["itemListElement"]:
                item = entry["item"]
                if item.get("publishedOn", {}).get("@id") != base + "#canal":
                    continue
                start, stop = (datetime.fromisoformat(item[key].replace("Z", "+00:00")) for key in ("startDate", "endDate"))
                if start.tzinfo is None or stop.tzinfo is None:
                    continue
                title = item["name"]
                if title == "Movie":
                    title = movies.get(urlsplit(item["url"]).fragment, "")
                yield start, stop, plain_html(title), None, item.get("description"), url


def cbs_current_rows(edition, now, snapshot):
    if edition == "CBSN-MIA":
        page = read_response(CBS_MIAMI_GUIDE, snapshot, "cbs-miami-pluto.html", user_agent=None).decode()
        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', page, re.S)
        if not match:
            return
        details = json.loads(match[1])["props"]["pageProps"]["ssrChannelDetails"]
        if details.get("originId") != 31218 or details.get("slug") != "cbs-news-miami":
            return
        for item in details["currentListing"]:
            start, stop = (datetime.fromisoformat(item[key].replace("Z", "+00:00"))
                           for key in ("startTimeFormatted", "endTimeFormatted"))
            yield start, stop, item["title"], None, item.get("description"), CBS_MIAMI_GUIDE
        return

    date = now.strftime("%Y%m%d")
    url = CBS_NATIONAL_GUIDE + "?" + urlencode({"lang": "en", "date": date, "channel_id": "464941"})
    guide = ET.fromstring(read_response(url, snapshot, f"cbs-national-{date}.xml"))
    channel = guide.find("./channel[@id='464941']")
    if channel is None or channel.findtext("display-name") != "CBS News National Stream":
        return
    for node in guide.findall("./programme[@channel='464941']"):
        start, stop = (datetime.strptime(node.get(key), XMLTV) for key in ("start", "stop"))
        yield start, stop, node.findtext("title"), None, node.findtext("desc"), url


def cbs_rows(edition, now, snapshot):
    url = CBS_RUNDOWN + "?" + urlencode({"edition": edition, "partner": "cbsnsite"})
    requests = (url,) if snapshot is not None else (url, f"{url}&_={int(now.timestamp())}")
    for locator in requests:
        nav = json.loads(read_response(locator, snapshot, f"cbs-{edition}.json"))["navigation"]
        updated = nav["dateUpdated"]
        if updated.get("timezone") != "UTC":
            return
        captured = datetime.fromisoformat(updated["date"]).replace(tzinfo=timezone.utc)
        if abs(captured - now) <= timedelta(minutes=15):
            break
    else:
        return
    live = [item for item in nav["data"] if item.get("edition") == edition and item.get("type") == "live"]
    if len(live) != 1:
        return
    live = live[0]
    start_live = datetime.fromisoformat(live["rundown_start_time"]).replace(tzinfo=timezone.utc)
    listed = nav["epgData"]
    listing_zone = None
    first_slot = 0
    if edition == "CBSN-MIA" and timedelta(0) <= captured - start_live <= timedelta(hours=2):
        # Miami may include prior rows and retain UTC-4 after the DST change.
        # Require a unique mapping to the exact edition's UTC rundown and title.
        anchors = {}
        for zone in (timezone(timedelta(hours=-4)), NEW_YORK):
            local_start = start_live.astimezone(zone)
            for index, item in enumerate(listed):
                if item.get("title") == live.get("epgSeriesName") and (
                    datetime.strptime(item["from"], "%I:%M:%S %p").time() == local_start.time()
                ):
                    anchors[(local_start.utcoffset(), index)] = zone
        if len(anchors) > 1:
            return
        if anchors:
            (_, first_slot), listing_zone = next(iter(anchors.items()))
    timeline_zone = listing_zone or NEW_YORK
    previous = (start_live if listing_zone else captured).astimezone(timeline_zone)
    future = []
    for item in listed[first_slot:]:
        start_time, stop_time = (datetime.strptime(item[key], "%I:%M:%S %p").time() for key in ("from", "to"))
        start = datetime.combine(previous.date(), start_time, timeline_zone)
        if start < previous:
            start += timedelta(days=1)
        stop = datetime.combine(start.date() + timedelta(days=(stop_time < start_time)), stop_time, timeline_zone)
        previous = start
        future.append((start, stop, item["title"], None, item.get("dek"), url))
    if listing_zone:
        current = [slot for slot in future if slot[0] <= now < slot[1]]
        if future[0][0].astimezone(timezone.utc) == start_live and (
            len(current) == 1 and current[0][2] == live.get("epgSeriesName")
        ) and all(
            left[0] < left[1] <= right[0] for left, right in zip(future, future[1:])
        ):
            yield from (slot for slot in future if slot[1] > now)
        return
    if not future or not now < future[0][0] <= now + timedelta(hours=2):
        return
    # When CBS omits the live slot, an exact-title match and both matching boundaries
    # keep platform-specific repeats out of the native channel guide.
    schedule = list(cbs_current_rows(edition, now, snapshot))
    for current in schedule:
        start, stop, title = current[:3]
        if start == start_live and start <= now < stop == future[0][0] and title == live.get("epgSeriesName"):
            yield current
            yield from future
            return
    if edition == "CBSN-US":
        try:
            page = read_response("https://www.cbsnews.com/live/", snapshot, "cbs-national-official.html",
                                 user_agent=None).decode()
        except (OSError, ValueError):
            return
        match = re.search(r'<h3 class="channel-guide-item__title">\s*CBS News 24/7\s*</h3>'
                          r'\s*<p class="channel-guide-item__description">([^<]+)</p>', page)
        if not match:
            return
        official_title = plain_html(match[1])
        current = [slot for slot in schedule if slot[0] == start_live and slot[0] <= now < slot[1]
                   and slot[2] == official_title]
        if len(current) == 1:
            yield current[0]
            reported = {row[:3] for row in future}
            yield from (slot for slot in schedule if slot[0] >= current[0][1] and slot[:3] in reported)


def europa_europa_rows(now, snapshot):
    today = now.astimezone(BUENOS_AIRES).date()
    days = {}
    for offset in (-1, 0, 1, 2):
        day = today + timedelta(days=offset)
        url = EUROPA_EUROPA_GUIDE + day.isoformat()
        try:
            page = read_response(url, snapshot, f"europa-{day}.html").decode()
        except (OSError, ValueError):
            continue
        heading = f"{WEEKDAYS_ES[day.weekday()]} {day.day} de {MONTHS_ES[day.month - 1]} en Europa Europa"
        if heading not in [plain_html(line) for line in re.findall(r"<h1>(.*?)</h1>", page, re.S)]:
            continue
        slots = []
        for clock, title in re.findall(r'<a class="itm\b[^"]*"[^>]*><span>(\d{2}\.\d{2})</span>(.*?)</a>', page, re.S):
            start = datetime.combine(day, datetime.strptime(clock, "%H.%M").time(), BUENOS_AIRES)
            slots.append((start, plain_html(title), url))
        if slots and all(left[0] < right[0] for left, right in zip(slots, slots[1:])):
            days[day] = slots
    for day, slots in days.items():
        for (start, title, url), (stop, _, _) in zip(slots, slots[1:] + days.get(day + timedelta(days=1), [])[:1]):
            yield start, stop, title, None, None, url


def ayn_rows(channel, now, snapshot):
    url = f"{AYN_GUIDE}{channel}/"
    page = read_response(url, snapshot, f"ayn-schedule-{channel}.html").decode()
    if f"<title>AYN TV - {AYN_NAMES[channel]} - جدول البرامج</title>" not in page:
        return
    today = now.astimezone(MUSCAT).date()
    active_catchup = re.search(r'<div class="tab-box" id="tab-1"[^>]*>(.*?)<div class="tab-box" id="tab-2"', page, re.S)
    if not active_catchup or f"/{today.isoformat()}/" not in active_catchup[1]:
        return
    grid = re.search(r'<section class="epg_sec">(.*?)</section>', page, re.S)
    if not grid:
        return
    slots = []
    for offset, section in enumerate(grid[1].split('<div class="epg_timeline_aside">')[1:]):
        day = today + timedelta(days=offset)
        if f"<h3>{AYN_WEEKDAYS[day.weekday()]}</h3>" not in section:
            return
        for item in re.split(r'<div class="epg_tl_item\s+epg_popup_detail[^"]*"[^>]*>', section)[1:]:
            title = re.search(r"<h2>(.*?)</h2>", item, re.S)
            clock = re.search(r"<p>\s*(\d{2}:\d{2})(?:\s*-\s*(\d{2}:\d{2}))?\s*</p>", item)
            if title and clock:
                start, stop = (datetime.combine(day, datetime.strptime(value, "%H:%M").time(), MUSCAT)
                               if value else None for value in clock.groups())
                slots.append((start, stop, plain_html(title[1])))
    for (start, end, title), (following, _, _) in zip(slots, slots[1:]):
        stop = end or following
        if title and title != "لا يوجد جدول" and start < stop <= following:
            yield start, stop, title, None, None, url


def dmax_rows(snapshot):
    data = json.loads(read_response(DMAX_SCHEDULE, snapshot, "dmax-page_guida-tv.json"))
    for block in data["blocks"]:
        if block.get("type") != "epgBlock":
            continue
        for channel in block["channels"]:
            identity = channel.get("channel", {})
            if (channel.get("uid"), identity.get("alternateId"), identity.get("countryCode")) != ("31", "dmax", "it"):
                continue
            slots = []
            for day in channel["days"]:
                for item in day["programs"]:
                    # The site labels its Italian wall clock as UTC and splits overnight slots.
                    start, stop = (datetime.fromisoformat(item[key]).replace(tzinfo=ROME)
                                   for key in ("start_dt", "end_dt"))
                    label = (item.get("show_name") or item.get("title"),
                             item.get("title") if item.get("show_name") else None,
                             item.get("description"))
                    full_slot = item.get("start_time"), item.get("end_time"), item.get("duration")
                    if (slots and slots[-1][1] == start and start.hour == start.minute == 0
                            and slots[-1][2:] == (label, full_slot)):
                        start = slots.pop()[0]
                    slots.append((start, stop, label, full_slot))
            for start, stop, label, _ in slots:
                yield start, stop, *label, DMAX_GUIDE


def ertnews_rows(now, snapshot):
    # ERT's programme day starts at 05:00 and includes the next civil morning.
    day = (now.astimezone(ATHENS) - timedelta(hours=5)).date()
    for offset in (0, 1):
        date = day + timedelta(days=offset)
        url = ERTNEWS_GUIDE + "?" + urlencode({"dt": date.isoformat()})
        try:
            page = read_response(url, snapshot, f"ertnews-{date}.html").decode()
        except (OSError, ValueError):
            continue
        if not re.search(r"<h1[^>]*>ΠΡΟΓΡΑΜΜΑ\s*ΕΡΤ NEWS</h1>", page):
            continue
        day_heading = re.search(r'<span class="section-title fs-lg">[^<]* (\d{2}/\d{2}/\d{4})</span>', page)
        if day_heading is None or day_heading[1] != date.strftime("%d/%m/%Y"):
            continue
        for article in re.finditer(r'<article id="broadcast_\d+"([^>]*)>(.*?)</article>', page, re.S):
            start = re.search(r'data-start-time="([^"]+)"', article[1])
            stop = re.search(r'data-end-time="([^"]+)"', article[1])
            title = re.search(r'<strong class="section-title fs-lg">(.*?)</strong>', article[2], re.S)
            if start and stop and title:
                begin, end = (datetime.strptime(field[1], "%Y-%m-%d %H:%M:%S%z")
                              for field in (start, stop))
                yield begin, end, plain_html(title[1]), None, None, url


def three_sat_rows(now, snapshot):
    today = now.astimezone(BERLIN).date()
    for offset in (-1, 0, 1):
        day = today + timedelta(days=offset)
        url = f"https://www.3sat.de/programm?airtimeDate={day.isoformat()}"
        try:
            page = read_response(url, snapshot, f"epg-emisoras-altas/3sat-{day}.html").decode()
        except (OSError, ValueError):
            continue
        for article in re.finditer(r"<article\b([^>]*)>(.*?)</article>", page, re.S):
            start_attr = re.search(r'\bdata-airtime-begin="([^"]+)"', article[1])
            stop_attr = re.search(r'\bdata-airtime-end="([^"]+)"', article[1])
            title = re.search(r"<h3\s+data-title[^>]*>(.*?)</h3>", article[2], re.S)
            if not (start_attr and stop_attr and title):
                continue
            try:
                start, stop = (datetime.fromisoformat(attr[1]) for attr in (start_attr, stop_attr))
            except ValueError:
                continue
            if start.tzinfo is None or stop.tzinfo is None:
                continue
            yield start, stop, plain_html(title[1]), None, None, url


def ktv_rows(now, snapshot):
    today = now.astimezone(SEOUL).date()
    days = {}
    for offset in (-1, 0, 1):
        day = today + timedelta(days=offset)
        url = "https://www.ktv.go.kr/broadChart/tv?" + urlencode({"date": day.strftime("%Y%m%d")})
        try:
            page = read_response(url, snapshot, f"epg-emisoras-altas/ktv-{day}.html").decode()
        except (OSError, ValueError):
            continue
        header = f'<h4 class="hide">{day:%Y.%m.%d}</h4>'
        schedule = re.search(r'<tbody id="schedulelist">(.*?)</tbody>', page, re.S)
        if header not in page or not schedule:
            continue
        slots = []
        for block in re.findall(r"<tr\b[^>]*>(.*?)</tr>", schedule[1], re.S):
            clock = re.search(r'<th class="date"[^>]*>\s*(\d\d:\d\d)\s*</th>', block)
            title = re.search(r'<div class="channel-cont">\s*<strong>(.*?)</strong>', block, re.S)
            if clock and title:
                start = datetime.combine(day, datetime.strptime(clock[1], "%H:%M").time(), SEOUL)
                slots.append((start, plain_html(title[1]), url))
        if slots and all(left[0] < right[0] for left, right in zip(slots, slots[1:])):
            days[day] = slots

    for day, slots in days.items():
        following = days.get(day + timedelta(days=1), [])
        next_slots = slots[1:] + following[:1]
        for (start, title, url), (stop, _, _) in zip(slots, next_slots):
            yield start, stop, title, None, None, url


def nrk_rows(channel, expected_name, now, snapshot):
    url = "https://tv.nrk.no/guide"
    page = read_response(url, snapshot, "epg-emisoras-altas/nrk-guide.html").decode()
    data = re.search(r'<script id="pageData" type="application/json">\s*(.*?)</script>', page, re.S)
    if not data:
        return
    state = json.loads(data[1])["initialState"]
    guide = state["epgs"][channel]
    if guide["title"] != expected_name:
        return
    for section in guide["sections"]:
        for entry in section["entries"]:
            if entry.get("title") == "Ingen sending":
                continue
            try:
                start, stop = (datetime.fromisoformat(entry[key].removeprefix("[Date]"))
                               for key in ("plannedStart", "plannedEnd"))
            except (AttributeError, KeyError, ValueError):
                continue
            if start.tzinfo is None or stop.tzinfo is None:
                continue
            yield start, stop, entry.get("title"), entry.get("subTitle"), entry.get("description"), url


def ruv_rows(channel, now, snapshot):
    today = now.astimezone(timezone.utc).date()
    for offset in (-1, 0, 1, 2):
        day = today + timedelta(days=offset)
        url = f"https://api.ruv.is/api/schedule/{channel}/{day}"
        try:
            schedule = json.loads(read_response(url, snapshot, f"{channel}-{day}.json"))
        except (OSError, ValueError):
            continue
        if schedule.get("channel") != channel or schedule.get("selected_iso") != f"{day}T00:00:00":
            continue
        for event in schedule.get("events", []):
            if event.get("is_header") or event.get("is_subevent"):
                continue
            try:
                start, stop = (datetime.fromisoformat(event[field]) for field in ("start_time", "end_time"))
            except (KeyError, TypeError, ValueError):
                continue
            if start.tzinfo is not None or stop.tzinfo is not None or start.date() != day:
                continue
            descriptions = event.get("description") or []
            description = " ".join(text for text in descriptions if isinstance(text, str))
            yield (start.replace(tzinfo=timezone.utc), stop.replace(tzinfo=timezone.utc),
                   event.get("title"), None, description, url)


def tunisia_tv_rows(channel, now, snapshot):
    day = now.astimezone(TUNIS).date()
    route = f"/ar/programme/{day.isoweekday()}/{channel}/"
    url = "https://www.tunisiatv.tn" + route + quote("الوطنية 2")
    page = read_response(url, snapshot, f"watania2-{day}.html").decode()
    if f'class="channel selected" href="{route}' not in page:
        return
    # The broadcaster serves its current day's grid even for other weekday URLs.
    # Retain only the day selected by the official site.
    for time_from, time_to, title, description in re.findall(
        r'<div class="desc"><time>(\d{2}:\d{2}) - (\d{2}:\d{2})</time>'
        r'<h3>(.*?)</h3><p>(.*?)</p></div>', page, re.S
    ):
        start, last_minute = (datetime.combine(day, datetime.strptime(clock, "%H:%M").time(), TUNIS)
                              for clock in (time_from, time_to))
        if last_minute < start:
            continue
        yield start, last_minute + timedelta(minutes=1), plain_html(title), None, plain_html(description), url


def meo_rows(channel, now, snapshot):
    today = now.astimezone(LISBON).date()
    for offset in (-1, 0, 1):
        day = today + timedelta(days=offset)
        query = urlencode({"callLetter": channel, "date": str(day), "userAgent": "IPTV_OFR_GTV"})
        url = f"{MEO_GUIDE}?{query}"
        try:
            schedule = json.loads(read_response(url, snapshot, f"meo-{channel.lower()}-{day}.json",
                                               user_agent=MEO_AGENT, headers=MEO_HEADERS))
        except (OSError, ValueError):
            continue
        if schedule.get("StatusCode") != 200:
            continue
        for row in schedule.get("Result", []):
            if row.get("CallLetter") != channel:
                continue
            title = (row.get("Title") or "").strip()
            if title.casefold() in {"", "sem título", "pausa na programação"}:
                continue
            try:
                start, stop = (datetime.fromisoformat(row[field]) for field in ("StartDate", "EndDate"))
            except (KeyError, TypeError, ValueError):
                continue
            if start.tzinfo is not None or stop.tzinfo is not None:
                continue
            local_start, local_stop = start.replace(tzinfo=LISBON), stop.replace(tzinfo=LISBON)
            if any(stamp.utcoffset() != stamp.replace(fold=1).utcoffset() for stamp in (local_start, local_stop)):
                continue
            yield local_start, local_stop, title, None, row.get("Synopsis"), url


def rudaw_rows(language, now, snapshot):
    today = now.astimezone(ERBIL).date()
    for offset in (-1, 0, 1):
        day = today + timedelta(days=offset)
        weekday = day.isoweekday() % 7
        query = urlencode({"lang": language, "day": weekday})
        url = "https://www.rudaw.net/api/schedules?" + query
        try:
            schedule = json.loads(read_response(url, snapshot, f"rudaw-schedule-{language}-{weekday}.json"))
        except (OSError, ValueError):
            continue
        if schedule.get("success") is not True:
            continue
        for row in schedule.get("data", []):
            if row.get("channel_id") != 1 or row.get("type") != "tv" or row.get("day") != weekday:
                continue
            try:
                start, stop = (datetime.combine(day, datetime.strptime(row[field], "%H:%M").time(), ERBIL)
                               for field in ("start_time", "end_time"))
            except (KeyError, TypeError, ValueError):
                continue
            if stop < start:
                stop += timedelta(days=1)
            yield start, stop, row.get("title"), None, row.get("description"), url


def ticker_news_rows(now, snapshot):
    url = "https://tickernews.co/where-to-watch/"
    page = read_response(url, snapshot, "ticker-news.html", user_agent="Mozilla/5.0").decode()
    if TICKER_NEWS_STREAM not in unescape(page).replace("\\/", "/"):
        return
    # The publisher computes "now" with a 120-minute Melbourne-time cycle.
    if not re.search(r"\(o\.hour\s*%\s*2\)\s*\*\s*60", page) or not re.search(r"nx\s*\+=\s*120", page):
        return
    grid = re.search(r'data-epg="([^"]+)"', page)
    if not grid:
        return
    document = json.loads(unescape(grid[1]))
    slots = document.get("slots")
    if document.get("tz") != "Australia/Melbourne" or not isinstance(slots, list):
        return
    if not slots or any(not isinstance(slot, dict) or type(slot.get("start")) is not int
                        or not isinstance(slot.get("name"), str) or not slot["name"].strip()
                        for slot in slots):
        return
    minutes = [slot["start"] for slot in slots]
    if minutes[0] != 0 or minutes[-1] >= 120 or sorted(set(minutes)) != minutes:
        return

    melbourne = ZoneInfo(document["tz"])
    by_minute = dict(zip(minutes, slots))
    hour = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    upcoming = []
    for hours in range(-4, 39):
        base = hour + timedelta(hours=hours)
        offset = base.astimezone(melbourne).hour % 2 * 60
        for minute in minutes:
            if offset <= minute < offset + 60:
                upcoming.append((base + timedelta(minutes=minute - offset), by_minute[minute]))
    for (start, slot), (stop, _) in zip(upcoming, upcoming[1:]):
        yield start, stop, slot["name"], None, slot.get("desc"), url


def osn_rows(channel, expected_name, now, snapshot):
    day = now.astimezone(DOHA).replace(hour=0, minute=0, second=0, microsecond=0)
    base = "https://www.osn.com/apidata/channel-day-schedule"
    for offset in range(2):
        start_day = day + timedelta(days=offset)
        end_day = start_day + timedelta(days=1)
        query = urlencode({"channelId": channel, "lang": "ar",
                           "startTime": int(start_day.timestamp() * 1000),
                           "endTime": int(end_day.timestamp() * 1000)})
        url = f"{base}?{query}"
        try:
            guide = json.loads(read_response(url, snapshot, f"osn-{channel}-{start_day.date()}.json"))
        except (OSError, ValueError):
            continue
        programmes = guide.get("programs") if isinstance(guide, dict) else None
        if not isinstance(programmes, list):
            continue
        for row in programmes:
            if not isinstance(row, dict) or row.get("channelId") != channel or row.get("channelName") != expected_name:
                continue
            try:
                begin, end = (datetime.fromtimestamp(row[field] / 1000, timezone.utc)
                              for field in ("startTime", "endTime"))
            except (KeyError, OverflowError, TypeError, ValueError):
                continue
            if start_day <= begin < end_day and begin < end:
                yield begin, end, row.get("programTitle"), None, row.get("programDescription"), base


def tabie_rows(channel, now, snapshot):
    day = now.astimezone(DOHA).date()
    query = urlencode({"channelId": channel, "date": str(day)})
    url = "https://tabie.net/Live/GetSchedule?" + query
    schedule = json.loads(read_response(url, snapshot, f"qtv-tabie-{day}.json"))
    if str(schedule.get("channelId")) != channel:
        return
    for row in schedule.get("programs", []):
        try:
            start, stop = (datetime.fromisoformat(row[field]) for field in ("startTime", "endTime"))
        except (KeyError, TypeError, ValueError):
            continue
        if start.tzinfo is None and stop.tzinfo is None and start.date() == day:
            yield start.replace(tzinfo=DOHA), stop.replace(tzinfo=DOHA), row.get("title"), None, None, url


def newsmax_balkans_rows(now, snapshot):
    today = now.astimezone(BELGRADE).date()
    for offset in (0, 1):
        day = today + timedelta(days=offset)
        query = urlencode({"dan": str(day)})
        url = "https://www.gledajtvuzivo.com/tv-program/newsmax-balkans/?" + query
        try:
            page = read_response(url, snapshot, f"newsmax-gledajtv-{day}.html").decode("utf-8")
        except (OSError, ValueError):
            continue
        if "<h1>NewsMax Balkans TV program</h1>" not in page:
            continue
        grid = re.search(r'<section\b[^>]*\bid="sched"[^>]*>(.*?)</section>', page, re.S)
        if not grid:
            continue
        for match in re.finditer(r'<li\b[^>]*\bdata-s="(\d{10})"\s+data-e="(\d{10})"[^>]*>\s*'
                                 r'<time>(\d\d:\d\d)</time>\s*<span><strong[^>]*>(.*?)</strong>',
                                 grid[1], re.S):
            start, stop = (datetime.fromtimestamp(int(match[index]), BELGRADE) for index in (1, 2))
            if start.date() <= day <= stop.date() and start.strftime("%H:%M") == match[3]:
                yield start, stop, plain_html(match[4]), None, None, url


def trt_world_rows(channel, now, snapshot):
    url = "https://www.trt.net.tr/api/epg?gmt=0&day=7"
    schedule = json.loads(read_response(url, snapshot, "trt-api-epg-gmt0.json"))
    today = now.astimezone(timezone.utc).date()
    for day in schedule.get("epg", []):
        date = datetime.strptime(day["date"], "%Y-%m-%d").date()
        if not today - timedelta(days=1) <= date <= today + timedelta(days=1):
            continue
        for edition in day.get("tvChannels", []):
            if str(edition.get("id")) != channel or edition.get("slug") != "trtworld":
                continue
            slots = edition.get("past", []) + [edition.get("current")] + edition.get("upcoming", [])
            for slot in filter(None, slots):
                try:
                    start, stop, source_date = (
                        datetime.fromisoformat(slot[field].replace("Z", "+00:00"))
                        for field in ("starttime", "endtime", "dateOfEpg")
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                if source_date.date() == date and all(
                    stamp.utcoffset() == timedelta(0) for stamp in (start, stop, source_date)
                ):
                    yield start, stop, slot.get("title"), None, None, url


def ard_epg_rows(channel, now, snapshot):
    service = {"name": "ARD alpha", "partner": channel}
    day = now.astimezone(ZoneInfo("Europe/Berlin")).date()
    for offset in (-1, 0, 1):
        date = (day + timedelta(days=offset)).isoformat()
        url = f"https://programm-api.ard.de/program/api/program?day={date}"
        try:
            data = json.loads(read_response(url, snapshot, f"ard-epg-{date}.data"))
        except (OSError, ValueError):
            continue
        if data.get("links", {}).get("self", {}).get("href") != url:
            continue
        services = [row for row in data.get("channels", [])
                    if row.get("id") == channel and row.get("publicationService") == service]
        if len(services) != 1:
            continue
        for block in services[0].get("timeSlots", []):
            for event in block:
                if (event.get("type") != "epg"
                        or event.get("channel") != {"id": channel, "name": "ARD alpha", "main_channel_id": channel}):
                    continue
                try:
                    start = datetime.fromisoformat(event["broadcastedOn"])
                    stop = datetime.fromisoformat(event["broadcastEnd"])
                except (KeyError, TypeError, ValueError):
                    continue
                if start.tzinfo is not None and stop.tzinfo is not None:
                    yield start, stop, event.get("coreTitle"), event.get("coreSubline"), event.get("synopsis"), url


def redetv_rows(now, snapshot):
    first_day = now.astimezone(BRASILIA).date()
    starts = {}
    for offset in (-1, 0, 1):
        day = first_day + timedelta(days=offset)
        date = day.isoformat()
        url = f"https://www.redetv.uol.com.br/Programacao?dia={date}"
        try:
            page = read_response(url, snapshot, f"redetv-{date}.data").decode("iso-8859-1")
        except OSError:
            continue
        active = re.search(r'<li\s+class=["\']?active["\']?[^>]*>\s*<button[^>]*onclick="atualizarDefinicao\(\'(\d{4}-\d{2}-\d{2})\'\)"', page)
        if not active or active[1] != date:
            continue
        programme_starts = re.findall(
            r'<div class="program-info">\s*<span>(\d{2}:\d{2})</span>\s*<h4>([^<]+)</h4>', page
        )
        previous = None
        for clock, title in programme_starts:
            start = datetime.combine(day, datetime.strptime(clock, "%H:%M").time(), BRASILIA)
            if previous is not None and start < previous:
                day += timedelta(days=1)
                start += timedelta(days=1)
            previous = start
            starts.setdefault(start, (" ".join(unescape(title).split()), url))
    ordered = sorted(starts)
    for start, stop in zip(ordered, ordered[1:]):
        title, source = starts[start]
        if stop - start <= timedelta(hours=4):
            yield start, stop, title, None, None, source


def cape_town_rows(now, snapshot):
    zone = ZoneInfo("Africa/Johannesburg")
    url = "https://freevisiontv.co.za/live/260/Cape-Town-TV"
    page = read_response(url, snapshot, "capetown-live.data").decode("utf-8")
    clock = re.search(r"var client_today = '(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2}';", page)
    date = now.astimezone(zone).date()
    if not clock or clock[1] != date.isoformat() or f'<meta property="og:url" content="{url}"' not in page:
        return
    section = page.partition("Today’s Schedule")[2].partition("</section>")[0]
    for item in section.split('<div class="item_wrap">')[1:]:
        title = re.search(r'<div class="show_title">\s*<p>([^<]+)</p>', item)
        duration = re.search(r'<div class="show_duration">\s*<p>(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})</p>', item)
        if title and duration:
            start, stop = (datetime.combine(date, datetime.strptime(value, "%H:%M").time(), zone)
                           for value in duration.groups())
            if stop < start:
                stop += timedelta(days=1)
            yield start, stop, unescape(title[1]), None, None, url


def wildearth_rows(channel, snapshot):
    official = "https://wildearth.tv/"
    page = read_response(official, snapshot, "wildearth-official.html").decode("utf-8")
    state = re.search(r'\bdata-page="([^"]+)"', page)
    if not state:
        return
    shows = json.loads(unescape(state[1])).get("props", {}).get("scheduledShows", [])
    anchors = {}
    for show in shows:
        try:
            start, stop = (datetime.fromisoformat(show[field].replace("Z", "+00:00"))
                           for field in ("start_time", "end_time"))
            if start.tzinfo is not None and stop.tzinfo is not None and start < stop:
                anchors[(start, stop)] = show["program_title"].strip().casefold()
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    url = f"https://epg.pw/api/epg.xml?channel_id={channel}"
    rows = list(xmltv_rows(url, "wildearth-zaf-epg.xml", channel, "WildEarth", snapshot, 500_000, 500_000))
    matched = {(start, stop) for start, stop, title, *_ in rows
               if anchors.get((start, stop)) == (title or "").strip().casefold()}
    if len(matched) < 2:
        return
    for row in rows:
        start, stop, title, *_ = row
        if anchors.get((start, stop)) == (title or "").strip().casefold() or not any(
            start < official_stop and official_start < stop for official_start, official_stop in anchors
        ):
            yield row


TVPASSPORT_STATIONS = {
    "3012": ("cnn-international-europemiddle-eastafrica", "CNNI"),
    "32823": ("law-crime-network", "L&C"),
    "1521": ("telemundo-eastern-feed", "MUN"),
}


class TVPassportListings(HTMLParser):
    def __init__(self, callsign):
        super().__init__()
        self.callsign = callsign
        self.timezone_options = False
        self.timezone = None
        self.listings = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select":
            self.timezone_options = attrs.get("id") == "timezone_selector"
        if tag == "option" and self.timezone_options and "selected" in attrs:
            self.timezone = attrs.get("value")
        if tag == "div" and attrs.get("data-callsign") == self.callsign and "data-st" in attrs:
            self.listings.append(attrs)

    def handle_endtag(self, tag):
        if tag == "select":
            self.timezone_options = False


def tvpassport_rows(channel, now, snapshot):
    slug, callsign = TVPASSPORT_STATIONS[channel]
    today = now.astimezone(NEW_YORK).date()
    for offset in (-1, 0, 1):
        day = today + timedelta(days=offset)
        url = f"https://www.tvpassport.com/tv-listings/stations/{slug}/{channel}/{day}"
        try:
            page = read_response(url, snapshot, f"tvpassport-{channel}-{day}.html").decode("utf-8")
        except (OSError, ValueError):
            continue
        guide = TVPassportListings(callsign)
        guide.feed(page)
        if guide.timezone != NEW_YORK.key:
            continue
        # TV Passport assigns each listing day from 06:00 ET to 06:00 ET next day.
        first = datetime.fromisoformat(f"{day}T06:00:00")
        for slot in guide.listings:
            try:
                local = datetime.strptime(slot["data-st"], "%Y-%m-%d %H:%M:%S")
                duration = int(slot["data-duration"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (first <= local < first + timedelta(days=1) and 0 < duration <= 1440):
                continue
            start = local.replace(tzinfo=NEW_YORK)
            if start.utcoffset() != start.replace(fold=1).utcoffset():
                continue
            start = start.astimezone(timezone.utc)
            stop = start + timedelta(minutes=duration)
            yield start, stop, slot.get("data-showname"), slot.get("data-episodetitle"), slot.get("data-description"), url


def ch3plus_rows(channel, now, snapshot):
    url = f"https://assets.ch3plus.com/schedule/{channel}.json"
    document = json.loads(read_response(url, snapshot, "ch3plus-33hd.json"))
    result = document.get("result", {})
    day = now.astimezone(BANGKOK).date()
    if document.get("code") != 200 or result.get("date") != day.strftime("%Y%m%d"):
        return
    rows = []
    for slot in result.get("schedules", []):
        if slot.get("day") != str(day) or slot.get("block") != "0" or slot.get("allow_country") != "ALL":
            continue
        try:
            start, stop = (datetime.strptime(f"{day} {slot[field]}", "%Y-%m-%d %H:%M").replace(tzinfo=BANGKOK)
                           for field in ("start", "end"))
        except (KeyError, ValueError):
            continue
        if stop < start:
            stop += timedelta(days=1)
        if stop > start and slot.get("title"):
            rows.append((start, stop, slot))
    for start, stop, slot in rows:
        # Short interstitials embedded inside a programme do not replace its main guide slot.
        if not any(outer_start <= start and stop <= outer_stop
                   and (outer_start, outer_stop) != (start, stop)
                   for outer_start, outer_stop, _ in rows):
            title = slot["title"]
            desc = slot.get("desc")
            yield start, stop, title, None, desc if desc != title else None, url


class ProgrammeCeSoirListings(HTMLParser):
    def __init__(self):
        super().__init__()
        self.listings = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.listings.extend(value for key, value in attrs if key == "data-program" and value)


def ct_shared_day(page, day):
    """ČT :D and ČT art broadcast on the same position at different hours."""
    date_path = day.strftime("%d.%m.%Y")
    if f'<link rel="canonical" href="https://www.ceskatelevize.cz/tv-program/{date_path}/"' not in page:
        return []
    prague = ZoneInfo("Europe/Prague")
    rows = {}
    for heading, block in re.findall(
            r'<h3 class="kotva">([^<]+)</h3>(?:(?!<h3).)*?<ul class="programmeList">(.*?)</ul>', page, re.S):
        channel = heading.rpartition(" na ")[2]
        if channel not in ("ČT :D", "ČT art"):
            continue
        after_midnight = channel == "ČT art" and "od 24:00" in heading
        for programme in re.findall(r'<li\b[^>]*>(.*?)</li>', block, re.S):
            clock = re.search(r'<span class="progTime">([0-2]\d):([0-5]\d)</span>', programme)
            title = re.search(r'<a\b[^>]*class="progTitle"[^>]*>(.*?)</a>', programme, re.S)
            length = re.search(r'<span class="stopaz"[^>]*>(\d{1,3}) minut', programme)
            if not (clock and title and length):
                continue
            hour, minute = map(int, clock.groups())
            if not ((channel == "ČT :D" and 6 <= hour < 20)
                    or (channel == "ČT art" and (hour < 6 if after_midnight else 20 <= hour < 24))):
                continue
            stamp = datetime.combine(day, datetime.min.time(), prague)
            start = stamp + timedelta(days=int(after_midnight), hours=hour, minutes=minute)
            boundary = stamp + timedelta(hours=20 if channel == "ČT :D" else 30)
            stop = min(start + timedelta(minutes=int(length[1])), boundary)
            text = plain_html(title[1])
            if text and start < stop:
                rows.setdefault(start, (stop, text))
    ordered = sorted(rows)
    output = []
    for index, start in enumerate(ordered):
        stop, title = rows[start]
        if index + 1 < len(ordered):
            # Start times are explicit; a rounded duration can overlap the next programme.
            stop = min(stop, ordered[index + 1])
        output.append((start, stop, title, None, None,
                       f"https://www.ceskatelevize.cz/tv-program/{date_path}/#5"))
    return output


def ct_shared_rows(now, snapshot):
    today = now.astimezone(ZoneInfo("Europe/Prague")).date()
    for offset in (-1, 0, 1, 2):
        day = today + timedelta(days=offset)
        try:
            page = read_response(f"https://www.ceskatelevize.cz/tv-program/{day:%d.%m.%Y}/", snapshot,
                                 f"ct-shared-{day.isoformat()}.html").decode("utf-8")
        except (OSError, UnicodeError, ValueError):
            continue
        yield from ct_shared_day(page, day)


DUBAI = ZoneInfo("Asia/Dubai")
SPACETOON_GUIDE = "https://www.filmtheque.com/en/guide/channel/space-toon-ae"


def space_toon_grid(page):
    services = []
    for payload in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
        try:
            node = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(node, dict) and node.get("@type") == "BroadcastService":
            services.append(node)
    if not any(node.get("name") == "Space Toon" and node.get("areaServed") == "United Arab Emirates"
               and node.get("url") == "https://spacetoon.com/" for node in services):
        return
    for stamp, clocks, title in re.findall(
            r'<li[^>]*>\s*<time[^>]*datetime="([^"]+)"[^>]*>(.*?)</time>\s*<span[^>]*>\s*<bdi[^>]*>([^<]+)</bdi>',
            page, re.I | re.S):
        try:
            start = datetime.strptime(stamp.split(" (", 1)[0], "%a %b %d %Y %H:%M:%S GMT%z").astimezone(DUBAI)
            displayed, end_time = re.findall(r"\d{2}:\d{2}", clocks)
            if start.strftime("%H:%M") != displayed:
                continue
            hour, minute = map(int, end_time.split(":"))
            stop = start.replace(hour=hour, minute=minute)
        except ValueError:
            continue
        if stop < start:
            stop += timedelta(days=1)
        if stop > start:
            yield start, stop, unescape(title).strip()


def space_toon_rows(now, snapshot):
    local_day = now.astimezone(DUBAI).date()
    for offset in (-1, 0, 1, 2):
        day = local_day + timedelta(days=offset)
        url = f"{SPACETOON_GUIDE}?day={day.isoformat()}"
        try:
            page = read_response(url, snapshot, f"spacetoon-{day.isoformat()}.html").decode("utf-8")
        except (OSError, UnicodeError, ValueError):
            continue
        for start, stop, title in space_toon_grid(page):
            if start.date() == day:
                yield start, stop, title, None, None, url


SENADO_CHILE_AGENDA = ("https://tramitacion.senado.cl/appsenado/index.php?"
                       "ac=citacionesComision&mo=comisiones&tipo_consulta=1")
SENADO_CHILE_MONTHS = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
                      "agosto", "septiembre", "octubre", "noviembre", "diciembre")


def senado_chile_rows(snapshot):
    page = read_response(SENADO_CHILE_AGENDA, snapshot, "senado-cl-citaciones-oficial.html").decode("utf-8")
    sessions = {}
    for heading, section in re.findall(
            r'<h1 class="titular serif">([^<]+)</h1>(.*?)(?=<h1 class="titular serif">|$)', page, re.S):
        date = re.fullmatch(r"\w+, (\d{1,2}) de (\w+) de (\d{4})", plain_html(heading).casefold())
        if not date or date[2] not in SENADO_CHILE_MONTHS:
            continue
        try:
            day = datetime(int(date[3]), SENADO_CHILE_MONTHS.index(date[2]) + 1, int(date[1]), tzinfo=SANTIAGO)
        except ValueError:
            continue
        for table in re.findall(r'<table\b[^>]*>(.*?)</table>', section, re.S):
            cells = {plain_html(field): value for field, value in re.findall(
                r'<th\b[^>]*>(.*?)</th>\s*<td\b[^>]*>(.*?)</td>', table, re.S)}
            committee = cells.get("COMISIÓN:", "")
            title = re.search(r'<strong\b[^>]*>(.*?)</strong>', committee, re.S)
            broadcast = re.search(r'<label\b[^>]*>(.*?)</label>', committee, re.S)
            if not title or not broadcast or "La sesión será transmitida por TV Senado y por el sitio" not in plain_html(broadcast[1]):
                continue
            if re.search(r"\b(suspendida|cancelada|postergada)\b", plain_html(table), re.I):
                continue
            hours = re.fullmatch(r"(\d{1,2}):(\d{2})\s+a\s+(\d{1,2}):(\d{2})", plain_html(cells.get("HORARIO:", "")))
            if not hours:
                continue
            try:
                start_h, start_m, stop_h, stop_m = map(int, hours.groups())
                start, stop = day.replace(hour=start_h, minute=start_m), day.replace(hour=stop_h, minute=stop_m)
            except ValueError:
                continue
            name = plain_html(title[1])
            if name and start < stop:
                label = name if name.casefold().startswith("comisión ") else f"Comisión {name}"
                sessions.setdefault((start, stop, label), plain_html(cells.get("MATERIA:", "")))
    ordered = sorted(sessions)
    for index, (start, stop, title) in enumerate(ordered):
        # The agenda may advertise two concurrent committees through different players.
        # The programme of the single linear feed is unknown for every conflicting window.
        if any(left < stop and start < right for i, (left, right, _) in enumerate(ordered) if i != index):
            continue
        yield start, stop, title, None, sessions[start, stop, title], SENADO_CHILE_AGENDA


def programmecesoir_rows(channel, expected_name, snapshot):
    for path, day in (("", "today"), ("demain/", "tomorrow")):
        url = f"https://programmecesoir.fr/{path}{channel}"
        try:
            page = read_response(url, snapshot, f"{channel}-{day}.html").decode("utf-8")
        except (OSError, ValueError):
            continue
        guide = ProgrammeCeSoirListings()
        guide.feed(page)
        for item in guide.listings:
            try:
                slot = json.loads(item)
                if slot.get("channelName") != expected_name:
                    continue
                start, stop = (datetime.fromisoformat(slot[field].replace("Z", "+00:00"))
                               for field in ("startTime", "endTime"))
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if start.tzinfo is not None and stop.tzinfo is not None:
                yield start, stop, slot.get("title"), None, slot.get("description"), url


def medi1tv_rows(channel, now, snapshot):
    today = now.astimezone(CASABLANCA).date()
    for offset in (0, 1):
        day = today + timedelta(days=offset)
        source_date = day.strftime("%d/%m/%Y")
        query = urlencode({"lg": "ar", "date": source_date, "type": channel}) + "&ss"
        url = f"https://idara.medi1tv.ma/rss/medi1tv/detail-grille2.aspx?{query}"
        try:
            entries = json.loads(read_response(url, snapshot, f"medi1-{channel}-{day}.json"))
        except (OSError, ValueError):
            continue
        if not isinstance(entries, list):
            continue
        slots = defaultdict(list)
        for entry in entries:
            if entry.get("date") != source_date or entry.get("langue") != "ar" or not entry.get("titre"):
                continue
            try:
                start = datetime.strptime(f"{source_date} {entry['heure']}", "%d/%m/%Y %H:%M").replace(
                    tzinfo=CASABLANCA)
            except (KeyError, ValueError):
                continue
            slots[start].append(entry)
        # The official guide publishes starts only. Do not extend the last title overnight.
        starts = sorted(slots)
        for start, stop in zip(starts, starts[1:]):
            if len(slots[start]) == 1:
                entry = slots[start][0]
                yield start, stop, entry["titre"], None, entry.get("resume"), url


def sportitalia_eit_rows(service, now, snapshot):
    query = urlencode({"char": 0, "file": f"./files/tca/TCA_CH44-MONTE-PORO_{service}.html", "live": 510})
    url = f"https://www.digitalbitrate.com/display_eit_schedule.php?{query}"
    page = read_response(url, snapshot, f"sportitalia-eit-{service}.html").decode("utf-8")
    scan = re.search(r"^Scan date\s*:\s*(\d{2}/\d{2}/\d{4} \d{2}:\d{2})", page.strip())
    if not scan:
        return
    captured = datetime.strptime(scan[1], "%d/%m/%Y %H:%M").replace(tzinfo=ROME)
    if not -timedelta(minutes=15) <= now - captured <= timedelta(hours=36):
        return
    dates = {day.strftime("%d/%m"): day
             for day in (captured.date() + timedelta(days=offset) for offset in range(-1, 5))}
    for html_row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, re.S):
        cols = [plain_html(cell) for cell in re.findall(r"<td\b[^>]*>(.*?)</td>", html_row, re.S)]
        if len(cols) < 6 or cols[3] != "ita" or cols[0] not in dates or not cols[4]:
            continue
        day = dates[cols[0]]
        try:
            start, stop = (datetime.combine(day, datetime.strptime(clock, "%Hh%M").time(), ROME)
                           for clock in cols[1].split(">"))
        except ValueError:
            continue
        if stop < start:
            stop += timedelta(days=1)
        if start < stop:
            yield start, stop, cols[4], None, cols[5], url


def tvplus_cnbc_rows(channel, snapshot):
    url = f"https://tvplus.com.br/programacao/{channel}"
    page = read_response(url, snapshot, f"tvplus-{channel}.html").decode("utf-8")
    for item in re.split(r'<div class="program">', page)[1:]:
        title = re.search(
            r'<a href="/sinopse/[0-9]+/CBC/([0-9]{2}h[0-9]{2})/(\d{4}-\d{2}-\d{2})/[^"]+"'
            r'[^>]*>(.*?)</a>', item, re.S)
        detail = re.search(r'<div class="details">\s*([0-9]{2}h[0-9]{2})\s*-\s*([0-9]{2}h[0-9]{2})', item)
        if not title or not detail or title[1] != detail[1]:
            continue
        try:
            start, stop = (datetime.strptime(f"{title[2]} {clock}", "%Y-%m-%d %Hh%M").replace(
                tzinfo=BRASILIA) for clock in detail.groups())
        except ValueError:
            continue
        if stop < start:
            stop += timedelta(days=1)
        epoch = re.search(r'data-start="(\d+)"\s+data-end="(\d+)"', item)
        if epoch and tuple(map(int, epoch.groups())) != (int(start.timestamp()), int(stop.timestamp())):
            continue
        if start < stop:
            yield start, stop, plain_html(title[3]), None, None, url


def wonder_uk_rows(now, snapshot):
    utc_day = now.astimezone(timezone.utc).date()
    for day in (utc_day, utc_day + timedelta(days=1)):
        url = f"{WONDER_UK_GUIDE}?day={day}"
        try:
            page = read_response(url, snapshot, f"wonder-uk-{day}.html").decode()
        except OSError:
            continue
        if f'<link rel="canonical" href="{WONDER_UK_GUIDE}"' not in page:
            continue
        for slot in re.finditer(r"<li\b[^>]*>(.*?)</li>", page, re.S):
            times = re.search(r'<time\b[^>]*\bdatetime="([^"]+)"[^>]*>(.*?)</time>', slot[1], re.I | re.S)
            if times is None:
                continue
            clocks = re.fullmatch(r"([0-9]{2}:[0-9]{2})\s*–\s*([0-9]{2}:[0-9]{2})", plain_html(times[2]))
            titles = re.findall(r'<bdi dir="auto"([^>]*)>(.*?)</bdi>', slot[1], re.S)
            if clocks is None or not titles or titles[0][0]:
                continue
            try:
                start = datetime.strptime(times[1].partition(" (")[0], "%a %b %d %Y %H:%M:%S GMT%z")
                end_clock = datetime.strptime(clocks[2], "%H:%M").time()
            except ValueError:
                continue
            # The time attribute is absolute; the printed clocks can be UTC or UK local time.
            display_start = start if start.strftime("%H:%M") == clocks[1] else start.astimezone(LONDON)
            if display_start.date() != day or display_start.strftime("%H:%M") != clocks[1]:
                continue
            end_day = day + timedelta(days=(clocks[2] < clocks[1]))
            display_stop = datetime.combine(end_day, end_clock, display_start.tzinfo)
            if display_stop.utcoffset() != display_stop.replace(fold=1).utcoffset():
                continue
            stop = display_stop.astimezone(timezone.utc)
            subtitle = next((plain_html(value).removeprefix("·").strip() for attrs, value in titles[1:]
                             if attrs == ' class="text-secondary"'), None)
            if start < stop:
                yield start, stop, plain_html(titles[0][1]), subtitle, None, WONDER_UK_GUIDE


def rakuten_uk_rows(channel, now, snapshot):
    start = (now - timedelta(hours=2)).astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    stop = start + timedelta(hours=38)
    query = {"classification_id": 18, "device_identifier": "web", "device_stream_audio_quality": "2.0",
             "device_stream_hdr_type": "NONE", "device_stream_video_quality": "FHD", "epg_duration_minutes": 360,
             "epg_starts_at": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "epg_starts_at_timestamp": int(start.timestamp()),
             "epg_ends_at": stop.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "epg_ends_at_timestamp": int(stop.timestamp()),
             "locale": "en", "market_code": "uk", "per_page": 50}
    for page in range(1, 5):
        url = "https://gizmo.rakuten.tv/v3/live_channels?" + urlencode(dict(query, page=page))
        payload = json.loads(read_response(url, snapshot, f"rakuten-uk-page-{page}.json", maximum=3_500_000))
        station = next((item for item in payload["data"] if item.get("id") == channel), None)
        if station is None:
            continue
        if not any(language.get("id") == "ENG" for language in station.get("labels", {}).get("languages", [])):
            return
        for slot in station.get("live_programs", []):
            try:
                begins, ends = (datetime.fromisoformat(slot[key].replace("Z", "+00:00"))
                                for key in ("starts_at", "ends_at"))
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if begins.tzinfo is None or ends.tzinfo is None:
                continue
            raw_title = slot.get("title")
            if not isinstance(raw_title, str):
                continue
            episode = re.fullmatch(r"(.+?)_\d{4}_\d+_\d+_(?:Episode \d+ - )?(.+)", raw_title)
            title, subtitle = episode.groups() if episode else (raw_title, slot.get("subtitle"))
            description = slot.get("description")
            yield begins, ends, title, subtitle, description if description != raw_title else None, url
        return


def canal_13_go_rows(channel, now, snapshot):
    url = f"https://cdn.rudo.video/assets/canal-13/playlists/{channel}/epg.json"
    payload = json.loads(read_response(f"{url}?{int(now.timestamp())}", snapshot, f"13go-epg-{channel}.json"))
    if (payload.get("channel"), str(payload.get("channelCode"))) != CANAL_13_GUIDES[channel]:
        return
    updated = datetime.fromisoformat(payload["updated"].replace("Z", "+00:00"))
    if updated.tzinfo is None or not -timedelta(minutes=15) <= now - updated <= timedelta(hours=36):
        return
    for slot in payload.get("events", []):
        try:
            start, stop = (datetime.fromisoformat(slot[key].replace("Z", "+00:00"))
                           for key in ("beginTime", "endTime"))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if start.tzinfo is not None and stop.tzinfo is not None:
            title = slot.get("title")
            subtitle = slot.get("episodeTitle")
            yield start, stop, title, subtitle if subtitle != title else None, slot.get("synopsis"), url


def tvusach_rows(now, snapshot):
    today = now.astimezone(timezone.utc).date()
    for offset in (-1, 0, 1, 2):
        day = today + timedelta(days=offset)
        stamp = int(datetime.combine(day, datetime.min.time(), timezone.utc).timestamp())
        # Janus groups day files by fixed 365-day epoch buckets.
        url = f"https://stv4.janus.cl/api/schedule/{stamp - stamp % 31_536_000}/{stamp}.json"
        try:
            payload = json.loads(read_response(url, snapshot, f"tvusach-schedule-{day}.json"))
        except (OSError, ValueError):
            continue
        for slot in payload.get("schedule", []):
            title = slot.get("title", "").strip()
            if not title or title.casefold() in {"stv", "tv usach"} or slot.get("programId") in (None, 0, 1):
                continue
            try:
                start, stop = (datetime.fromtimestamp(slot[field], timezone.utc) for field in ("start", "end"))
            except (KeyError, OSError, OverflowError, TypeError, ValueError):
                continue
            yield start, stop, title, slot.get("subtitle"), slot.get("review"), url


def pbo_rows(now, snapshot):
    url = "https://pbo.pe/programacion/"
    page = read_response(url, snapshot, "pbo-programacion.html").decode("utf-8")
    if "Hora de Lima" not in page or "TV, radio 91.9 FM y digital en simultáneo" not in page:
        return
    sections = re.split(r'<div class="schedule__panel"[^>]*\bdata-day="([1-7])"[^>]*>', page)
    week = dict(zip(sections[1::2], sections[2::2]))
    today = now.astimezone(LIMA).date()
    for offset in (-1, 0, 1, 2):
        day = today + timedelta(days=offset)
        origin = datetime.combine(day, datetime.min.time(), LIMA)
        for first, last, card in re.findall(
                r'<li class="schedule__item[^>]*\bdata-start="(\d+)"\s+data-end="(\d+)"[^>]*>(.*?)</li>',
                week.get(str(day.isoweekday()), ""), re.S):
            # PBO's broadcast day ends at 05:00 the next civil day.
            first, last = int(first), int(last)
            title = re.search(r"<strong>(.*?)</strong>", card, re.S)
            if not title or not 300 <= first < last <= 1740:
                continue
            subtitle = "Repetición" if 'class="schedule__repe"' in card else None
            detail = re.search(r"<em>(.*?)</em>", card, re.S)
            yield (origin + timedelta(minutes=first), origin + timedelta(minutes=last), plain_html(title[1]),
                   subtitle, plain_html(detail[1]) if detail else None, url)


TRAVELTV_MONTHS = (
    "януари", "февруари", "март", "април", "май", "юни",
    "юли", "август", "септември", "октомври", "ноември", "декември",
)
TRAVELTV_DAYS = ("понеделник", "вторник", "сряда", "четвъртък", "петък", "събота", "неделя")


def traveltv_table(page, local_day, first_minute, last_minute):
    headings = re.findall(r"<h4\b[^>]*>(.*?)</h4>", page, re.S)
    interval = r"(\d{1,2}) (\w+) \((\w+)\) [–—-] (\d{1,2}) (\w+) \((\w+)\)"
    endpoints = next((match for heading in headings
                      if (match := re.fullmatch(interval, plain_html(heading).lower()))), None)
    if endpoints is None:
        return {}
    available = []
    for day, month, weekday in (endpoints.groups()[:3], endpoints.groups()[3:]):
        if month not in TRAVELTV_MONTHS or weekday not in TRAVELTV_DAYS:
            return {}
        candidates = []
        for year in range(local_day.year - 1, local_day.year + 2):
            try:
                candidates.append(local_day.replace(year=year, month=TRAVELTV_MONTHS.index(month) + 1,
                                                    day=int(day)))
            except ValueError:
                continue
        if not candidates:
            return {}
        exact = min(candidates, key=lambda candidate: abs(candidate - local_day))
        if exact.weekday() != TRAVELTV_DAYS.index(weekday):
            return {}
        available.append(exact)
    begins, ends = available
    if not begins <= local_day <= ends or ends - begins > timedelta(days=7):
        return {}

    tables = dict()
    for style, markup in re.findall(r'''<table class=['"]vertical(header|1)['"]>(.*?)</table>''', page, re.S):
        if style == "header":
            times = re.findall(r"<th>(\d{1,2}):(\d{2})</th>", markup)
            clock = [60 * int(hour) + int(minute) for hour, minute in times]
            if clock != list(range(first_minute, last_minute, 30)):
                return {}
            tables["header"] = clock
            continue
        weekday = re.search(r'''<th class=['"]rowheader['"]>(.*?)</th>''', markup, re.S)
        if weekday:
            tables[plain_html(weekday[1]).lower()] = markup
    clock = tables.get("header", [])
    markup = tables.get(TRAVELTV_DAYS[local_day.weekday()], "")
    slots = re.findall(r'''<tr class=['"]datarow([1-9]\d*)['"]>(.*?)</tr>''', markup, re.S)
    if not clock or sum(int(count) for count, _ in slots) != len(clock):
        return {}
    titles = {}
    offset = 0
    for count, cell in slots:
        stop = offset + int(count)
        title = re.search(r'''<div class=['"]ws-item-title[^'"]*['"]>(.*?)</div>''', cell, re.S)
        if title:
            value = plain_html(title[1])
            if value and "\ufffd" not in value:
                titles[(clock[offset], clock[stop - 1] + 30)] = value
        offset = stop
    return titles


def traveltv_rows(now, snapshot):
    sofia = ZoneInfo("Europe/Sofia")
    today = now.astimezone(sofia).date()
    dates = [today + timedelta(days=offset) for offset in (-1, 0, 1, 2)]
    for first, last, pages in (
        (0, 480, ("night-shows-this-week", "night-shows-next-week")),
        (480, 1440, ("programme", "next-week")),
    ):
        remaining = dates.copy()
        for name in pages:
            url = f"https://tv.traveltv.bg/{name}/"
            try:
                page = read_response(url, snapshot, f"traveltv-{name}.html").decode("utf-8", errors="replace")
            except (OSError, ValueError):
                continue
            for day in remaining.copy():
                slots = traveltv_table(page, day, first, last)
                if slots:
                    remaining.remove(day)
                origin = datetime(day.year, day.month, day.day, tzinfo=sofia)
                for (first_minute, last_minute), title in slots.items():
                    start, stop = (origin + timedelta(minutes=minute) for minute in (first_minute, last_minute))
                    if any(moment.utcoffset() != moment.replace(fold=1).utcoffset() for moment in (start, stop)):
                        continue
                    yield start, stop, title, None, None, url


def america_tv_guide_rows(provider, now, snapshot):
    channel_path, filename, local_zone, zone_label, broadcaster_link, off_air = AMERICA_TV_GUIDES[provider]
    url = f"https://americatvguide.com{channel_path}"
    page = read_response(url, snapshot, filename).decode("utf-8")
    if zone_label not in page or (broadcaster_link and broadcaster_link not in page):
        return
    local_day = now.astimezone(local_zone).date()
    slots = {}
    for date, body in re.findall(
            r"<h5>(?:Hoy|Mañana) - (\d{1,2}/\d{1,2}/\d{2}) - [^<]+</h5>(.*?)</table>", page, re.S):
        day = datetime.strptime(date, "%d/%m/%y").date()
        if abs((day - local_day).days) > 1:
            continue
        for link_time, hour, minute, title in re.findall(
                rf'<a href="{re.escape(channel_path)}/{day.day:02}([0-2]\d[0-5]\d)/[^"]+"'
                r' title="([01]\d|2[0-3]):([0-5]\d) ([^"]+)">', body):
            if link_time == hour + minute:
                start = datetime(day.year, day.month, day.day, int(hour), int(minute), tzinfo=local_zone)
                slots.setdefault(start, plain_html(title))
    dates = sorted(slots)
    for start, stop in zip(dates, dates[1:]):
        title = slots[start]
        if title and title.casefold() not in off_air:
            yield start, stop, title, None, None, url


class RedUnoTables(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.tables = []
        self.depth = 0
        self.row = self.cell = None
        self.feed(page)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.depth += 1
            if self.depth == 1:
                self.tables.append([])
        if self.depth != 1:
            return
        if tag == "tr":
            self.row = []
        if tag == "td" and self.row is not None:
            attrs = dict(attrs)
            self.cell = ([], int(attrs.get("rowspan", 1)), int(attrs.get("colspan", 1)))

    def handle_endtag(self, tag):
        if self.depth == 1:
            if tag == "td" and self.cell is not None:
                parts, height, width = self.cell
                self.row.append((" ".join("".join(parts).split()), height, width))
                self.cell = None
            if tag == "tr" and self.row is not None:
                self.tables[-1].append(self.row)
                self.row = self.cell = None
        if tag == "table":
            self.depth -= 1

    def handle_data(self, data):
        if self.depth == 1 and self.cell is not None:
            self.cell[0].append(data)


def reduno_title(value):
    return normalize("NFKD", value.casefold()).encode("ascii", "ignore").decode().rstrip(".")


def reduno_dated_rows(page, now):
    built = re.findall(r"<!-- /programacion \| [^>]+ \| (\d{4}-\d\d-\d\dT[^ ]+Z) -->", page)
    if len(built) != 1:
        return
    build_date = datetime.fromisoformat(built[0].replace("Z", "+00:00"))
    if not now - timedelta(days=8) <= build_date <= now:
        return
    today = now.astimezone(LA_PAZ).date()
    days = {reduno_title(f"{WEEKDAYS_ES[day.weekday()]} {day.day} de {MONTHS_ES[day.month - 1]}"): day
            for day in (today + timedelta(days=offset) for offset in (-1, 0, 1, 2))}
    for table in RedUnoTables(page).tables:
        first_row = next((index for index, row in enumerate(table) if row and row[0][0] == "HORA"), None)
        if first_row is None:
            continue
        header = table[first_row]
        if any(cell[1:] != (1, 1) for cell in header):
            continue
        columns = {}
        for column, (title, _, _) in enumerate(header):
            if title == "HORA":
                clock_column = column
            elif (day := days.get(reduno_title(title))) is not None:
                columns[column] = day, clock_column
        occupied_until = {}
        slots = defaultdict(list)
        for row_index, row in enumerate(table[first_row + 1:]):
            starts = {}
            for title, height, width in row:
                column = next((column for column in range(len(header))
                               if occupied_until.get(column, 0) <= row_index), None)
                if column is None or width != 1 or height < 1:
                    break
                occupied_until[column] = row_index + height
                starts[column] = title
            else:
                for column, (_, clock_column) in columns.items():
                    clock, title = starts.get(clock_column), starts.get(column)
                    if clock and title and re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", clock):
                        slots[column].append((datetime.strptime(clock, "%H:%M").time(), title))
                continue
            # A merged cell in the schedule body cannot safely be attributed to one edition.
            slots.clear()
            break
        for column, values in slots.items():
            day = broadcast_day = columns[column][0]
            dated = []
            for clock, title in values:
                start = datetime.combine(day, clock, LA_PAZ)
                if dated and start <= dated[-1][0]:
                    if dated[-1][0].hour >= 18 and start.hour < 6:
                        start += timedelta(days=1)
                        day += timedelta(days=1)
                    else:
                        break
                dated.append((start, title))
            else:
                for (start, title), (stop, _) in zip(dated, dated[1:]):
                    yield broadcast_day, start, stop, title


def reduno_tariff_slots(page):
    for table in RedUnoTables(page).tables:
        if not table:
            continue
        heading = [cell[0] for cell in table[0]]
        if len(heading) != 7 or heading[0] != "Hora" or heading[4] != "SCZ":
            continue
        day = reduno_title(heading[1])
        weekdays = (range(5) if day == "lunes a viernes" else
                    [i for i, name in enumerate(WEEKDAYS_ES) if reduno_title(name) == day])
        for row in table[1:]:
            if len(row) != 7 or not re.fullmatch(r"\d+(?:\.\d+)?", row[4][0]):
                continue
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d-(?:[01]\d|2[0-3]):[0-5]\d", row[0][0]):
                continue
            start, stop = (datetime.strptime(clock, "%H:%M").time() for clock in row[0][0].split("-"))
            for weekday in weekdays:
                yield weekday, start, stop, reduno_title(row[1][0])


def reduno_rows(now, snapshot):
    dated = read_response(RED_UNO_GUIDE, snapshot, "reduno-scz-dated.html").decode("utf-8")
    tariff = read_response(RED_UNO_SCZ_TARIFF, snapshot, "reduno-scz-tariff.html").decode("utf-8")
    months = {reduno_title(heading) for heading in re.findall(
        r"<h2>\s*<strong>\s*([^<]+?)\s*</strong>\s*</h2>", tariff, re.I)
        if reduno_title(heading) in MONTHS_ES}
    if len(months) != 1:
        return
    confirmed = set(reduno_tariff_slots(tariff))
    for day, start, stop, title in reduno_dated_rows(dated, now):
        key = day.weekday(), start.time(), stop.time(), reduno_title(title)
        if MONTHS_ES[day.month - 1] in months and key in confirmed:
            yield start, stop, title, None, None, RED_UNO_GUIDE


def eldeber_rows(now, snapshot):
    url = "https://eldeber.com.bo/"
    page = read_response(url, snapshot, "eldeber-home.html").decode("utf-8")
    schedule = re.search(r"var schedule = (\{.*?\});", page, re.S)
    if not schedule or 'timeZone: "America/La_Paz"' not in page:
        return
    week = json.loads(schedule[1])
    weekdays = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
    if set(week) != set(weekdays):
        return
    # The homepage initially retained the older schedule after the dated announcement.
    monday = week["Lunes"]
    if not any(title.strip().casefold() == "dinero" and interval == "20:00 a 21:00 hrs."
               for title, interval in monday):
        return
    la_paz = ZoneInfo("America/La_Paz")
    today = now.astimezone(la_paz).date()
    # EL DEBER announced this schedule begins Monday, November 9.
    effective = datetime(2026, 11, 9).date()
    for offset in (-1, 0, 1, 2):
        day = today + timedelta(days=offset)
        if day < effective:
            continue
        origin = datetime(day.year, day.month, day.day, tzinfo=la_paz)
        for title, interval in week[weekdays[day.weekday()]]:
            match = re.fullmatch(r"([0-2]\d):([0-5]\d) a ([0-2]\d):([0-5]\d) hrs\.", interval)
            if not match or title.casefold().startswith("programación variada"):
                continue
            sh, sm, eh, em = map(int, match.groups())
            first, last = sh * 60 + sm, eh * 60 + em
            if last == 0:
                last = 1440
            if 0 <= first < last <= 1440:
                yield origin + timedelta(minutes=first), origin + timedelta(minutes=last), title, None, None, url


def epgshare_rows(region, channel, expected_name, snapshot):
    filename = f"epg_ripper_{region}.xml.gz"
    url = f"https://epgshare01.online/epgshare01/{filename}"
    compressed_limit, xml_limit = {"AT1": (5_000_000, 24_000_000),
                                   "AU1": (5_000_000, 38_000_000),
                                   "DE1": (5_500_000, 32_000_000),
                                   "ES1": (3_500_000, 24_000_000),
                                   "IT1": (2_500_000, 18_000_000),
                                   "CZ1": (7_000_000, 42_000_000)}.get(region, (2_500_000, 12_000_000))
    yield from xmltv_rows(url, filename, channel, expected_name, snapshot, compressed_limit, xml_limit)


def xmltv_rows(url, filename, channel, expected_name, snapshot, compressed_limit, xml_limit):
    data = read_response(url, snapshot, filename, maximum=compressed_limit)
    if filename.endswith(".gz"):
        with gzip.GzipFile(fileobj=BytesIO(data)) as archive:
            data = archive.read(xml_limit + 1)
    if len(data) > xml_limit:
        raise ValueError("El XMLTV regional supera el tamaño permitido")
    events = ET.iterparse(BytesIO(data), events=("start", "end"))
    _, root = next(events)
    if root.tag != "tv":
        return
    depth = 1
    names = []
    rows = []
    for event, entry in events:
        if event == "start":
            depth += 1
        else:
            if depth == 2:
                if entry.tag == "channel" and entry.get("id") == channel:
                    names.append(entry.findtext("display-name"))
                elif entry.tag == "programme" and entry.get("channel") == channel:
                    start, stop = (datetime.strptime(entry.attrib[key], XMLTV) for key in ("start", "stop"))
                    rows.append((start, stop, entry.findtext("title"), entry.findtext("sub-title"),
                                 entry.findtext("desc"), url))
                root.remove(entry)
            depth -= 1
    if names == [expected_name]:
        yield from rows


def normalized_programmes(feed, provider_id, rows, now):
    unique = {}
    for start, stop, title, subtitle, description, provenance in rows:
        if not isinstance(title, str) or not title.strip():
            continue
        if not (now - timedelta(days=1) <= start < stop <= start + timedelta(hours=12)
                and now - timedelta(hours=2) < stop <= now + timedelta(days=3)):
            continue
        unique.setdefault((start, stop, title.strip()), (subtitle, description, provenance))
    ordered = sorted(unique)
    programmes = []
    for index, (start, stop, title) in enumerate(ordered):
        if ((index and ordered[index - 1][1] > start)
                or (index + 1 < len(ordered) and ordered[index + 1][0] < stop)):
            continue
        subtitle, description, provenance = unique[start, stop, title]
        node = ET.Element("programme", {
            "channel": feed, "start": start.astimezone(timezone.utc).strftime(XMLTV),
            "stop": stop.astimezone(timezone.utc).strftime(XMLTV),
            "zapper-provenance": provenance, "zapper-provider-id": provider_id,
        })
        for tag, text in (("title", title), ("sub-title", subtitle), ("desc", description)):
            if isinstance(text, str) and text.strip():
                ET.SubElement(node, tag).text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text.strip())
        programmes.append(node)
    return programmes


def programmes_for_expansion(entries, prior_xml, now, fetch=False, snapshot=None):
    bindings = {row["tvgID"]: resolved for row in entries if (resolved := binding(row))}
    prior = defaultdict(list)
    if prior_xml:
        for programme in ET.fromstring(prior_xml).findall("programme"):
            feed = programme.get("channel")
            match = bindings.get(feed)
            if match and programme.get("zapper-provider-id") == ":".join(match[:2]):
                if datetime.strptime(programme.attrib["stop"], XMLTV) > now - timedelta(hours=2):
                    prior[feed].append(programme)

    def collect(item):
        feed, (provider, key, name) = item

        def usable(programmes):
            return programme_state(programmes, now) in VERIFIED_STATES

        if fetch or snapshot is not None:
            try:
                if provider == "pluto":
                    rows = pluto_rows(key, now, snapshot)
                elif provider == "rai":
                    rows = rai_rows(key, name, now, snapshot)
                elif provider == "rtve-star":
                    rows = rtve_star_rows(key, snapshot)
                elif provider == "tvl":
                    rows = tvl_rows(now, snapshot)
                elif provider == "senado-br":
                    rows = senado_br_rows(now, snapshot)
                elif provider == "3sat":
                    rows = three_sat_rows(now, snapshot)
                elif provider == "ktv":
                    rows = ktv_rows(now, snapshot)
                elif provider == "nrk":
                    rows = nrk_rows(key, name, now, snapshot)
                elif provider == "ruv":
                    rows = ruv_rows(key, now, snapshot)
                elif provider == "tunisiatv":
                    rows = tunisia_tv_rows(key, now, snapshot)
                elif provider == "meo":
                    rows = meo_rows(key, now, snapshot)
                elif provider == "rudaw":
                    rows = rudaw_rows(key, now, snapshot)
                elif provider == "osn":
                    rows = osn_rows(key, "Al Araby TV", now, snapshot)
                elif provider == "tabie":
                    rows = tabie_rows(key, now, snapshot)
                elif provider == "gledajtv":
                    rows = newsmax_balkans_rows(now, snapshot)
                elif provider == "trt-portal":
                    rows = trt_world_rows(key, now, snapshot)
                elif provider == "ard-epg":
                    rows = ard_epg_rows(key, now, snapshot)
                elif provider == "redetv":
                    rows = redetv_rows(now, snapshot)
                elif provider == "freevision-cape-town":
                    rows = cape_town_rows(now, snapshot)
                elif provider == "wildearth":
                    rows = wildearth_rows(key, snapshot)
                elif provider == "medi1tv":
                    rows = medi1tv_rows(key, now, snapshot)
                elif provider == "streamingtvguides-lb":
                    rows = otv_lebanon_rows(snapshot)
                elif provider == "reduno":
                    rows = reduno_rows(now, snapshot)
                elif provider == "eldeber":
                    rows = eldeber_rows(now, snapshot)
                elif provider in AMERICA_TV_GUIDES:
                    rows = america_tv_guide_rows(provider, now, snapshot)
                elif provider == "traveltv":
                    rows = traveltv_rows(now, snapshot)
                elif provider == "pantallazo":
                    rows = mega_ficcion_rows(now, snapshot)
                elif provider == "pbo":
                    rows = pbo_rows(now, snapshot)
                elif provider == "13go":
                    rows = canal_13_go_rows(key, now, snapshot)
                elif provider == "tvusach":
                    rows = tvusach_rows(now, snapshot)
                elif provider == "filmtheque-uk":
                    rows = wonder_uk_rows(now, snapshot)
                elif provider == "rakuten-uk":
                    rows = rakuten_uk_rows(key, now, snapshot)
                elif provider == "tvplus-br":
                    rows = tvplus_cnbc_rows(key, snapshot)
                elif provider == "digitalbitrate":
                    rows = sportitalia_eit_rows(key, now, snapshot)
                elif provider == "ct-shared":
                    rows = ct_shared_rows(now, snapshot)
                elif provider == "filmtheque-ae":
                    rows = space_toon_rows(now, snapshot)
                elif provider == "senado-chile":
                    rows = senado_chile_rows(snapshot)
                elif provider == "ticker-news":
                    rows = ticker_news_rows(now, snapshot)
                elif provider == "programmecesoir":
                    rows = programmecesoir_rows(key, {
                        "2m-maroc": "2M Maroc", "trace-latina": "Trace Latina", "tv5-monde-info": "TV5 Monde Info",
                    }[key], snapshot)
                elif provider == "ch3plus":
                    rows = ch3plus_rows(key, now, snapshot)
                elif provider == "tvpassport":
                    rows = tvpassport_rows(key, now, snapshot)
                elif provider == "apunt":
                    rows = apunt_rows(snapshot)
                elif provider == "ert":
                    rows = ertnews_rows(now, snapshot)
                elif provider == "mediaset":
                    rows = mediaset_rows(key, name, now, snapshot)
                elif provider == "once":
                    rows = once_rows(key, snapshot)
                elif provider == "telemetro":
                    rows = telemetro_rows(now, snapshot)
                elif provider == "tvp":
                    rows = tvp_rows(key, now, snapshot)
                elif provider == "tc":
                    rows = tc_rows(now, snapshot)
                elif provider == "oromar":
                    rows = oromar_rows(key, now, snapshot)
                elif provider == "utalca":
                    rows = utalca_rows(now, snapshot)
                elif provider == "rtvd":
                    rows = rtvd_rows(now, snapshot)
                elif provider == "citynews-vancouver":
                    rows = citynews_vancouver_rows(key, snapshot)
                elif provider == "bbs":
                    rows = bbs_rows(key, now, snapshot)
                elif provider == "mdr":
                    rows = mdr_rows(now, snapshot)
                elif provider == "iranintl":
                    rows = iranintl_rows(key, snapshot)
                elif provider == "rcntotal":
                    rows = rcntotal_rows(key, snapshot)
                elif provider == "lionsgate":
                    rows = xmltv_rows("https://lionsgatechannels.com/assets/json/MOVSP.xml", "moviesphere.xml",
                                      key, "MovieSphere", snapshot, 2_500_000, 2_500_000)
                elif provider == "shahid":
                    rows = shahid_rows(key, now, snapshot)
                elif provider == "asharq-now":
                    rows = asharq_now_rows(key, now, snapshot)
                elif provider == "chada":
                    rows = chada_rows(now, snapshot)
                elif provider == "ntv":
                    rows = ntv_rows(now, snapshot)
                elif provider == "sky-nz" and key == "hgtv":
                    rows = sky_nz_hgtv_rows(now, snapshot)
                elif provider == "tvonline" and key == "tv-peru-national":
                    rows = tvperu_national_rows(snapshot)
                elif provider == "nhk-world-premium":
                    rows = nhk_world_premium_rows(now, snapshot)
                elif provider == "caracol":
                    rows = caracol_rows(now, snapshot)
                elif provider == "maghribia":
                    rows = maghribia_rows(now, snapshot)
                elif provider == "allegro":
                    rows = allegro_rows(now, snapshot)
                elif provider == "eska":
                    rows = eska_rows(snapshot)
                elif provider == "star-gr":
                    rows = star_greece_rows(now, snapshot)
                elif provider == "imagen-mx":
                    rows = formula_rows("imagen-television", snapshot)
                elif provider == "formula-mx":
                    rows = formula_rows(key, snapshot)
                elif provider == "formula-co":
                    rows = formula_rows(key, snapshot, market="co")
                elif provider == "samsung-ca":
                    rows = xmltv_rows(SAMSUNG_CA_XMLTV, "samsung-ca.xml.gz", key, "Stingray Classica",
                                      snapshot, 2_500_000, 12_000_000)
                elif provider == "samsung-de":
                    rows = xmltv_rows(SAMSUNG_DE_XMLTV, "samsung-de.xml.gz", key, "Terra Mater WILD",
                                      snapshot, 2_500_000, 12_000_000)
                elif provider == "samsung-gb":
                    rows = xmltv_rows(SAMSUNG_GB_XMLTV, "samsung-gb.xml.gz", key, "INTRAVEL",
                                      snapshot, 2_500_000, 12_000_000)
                elif provider == "tubi-love-nature":
                    rows = xmltv_rows(TUBI_XMLTV, "tubi-love-nature.xml", key, "Love Nature",
                                      snapshot, 5_000_000, 5_000_000)
                elif provider == "tubi-estrella":
                    rows = xmltv_rows(TUBI_XMLTV, "tubi-estrella.xml", key, "Estrella News",
                                      snapshot, 5_000_000, 5_000_000)
                elif provider == "plex-au":
                    rows = xmltv_rows(PLEX_AU_XMLTV, "plex-au.xml.gz", key, PLEX_AU_NAMES[feed],
                                      snapshot, 2_500_000, 12_000_000)
                elif provider == "cbs":
                    rows = cbs_rows(key, now, snapshot)
                elif provider == "europa-europa":
                    rows = europa_europa_rows(now, snapshot)
                elif provider == "ayn":
                    rows = ayn_rows(key, now, snapshot)
                elif provider == "dmax-it":
                    rows = dmax_rows(snapshot)
                elif provider == "sydney":
                    rows = xmltv_rows(SYDNEY_XMLTV, "sydney-epg.xml.gz", key, name, snapshot, 2_500_000, 12_000_000)
                elif provider.startswith("epgshare-"):
                    rows = epgshare_rows(EPGSHARE[feed][0], key, name, snapshot)
                else:
                    rows = rtve_rows(now, snapshot)
                programmes = normalized_programmes(feed, f"{provider}:{key}", rows, now)
                if usable(programmes):
                    return feed, programmes
            except (EOFError, ET.ParseError, KeyError, OSError, TypeError, ValueError):
                pass
        previous = prior.get(feed, [])
        return feed, previous if usable(previous) else []

    with ThreadPoolExecutor(max_workers=6) as workers:
        return {feed: rows for feed, rows in workers.map(collect, bindings.items()) if rows}
