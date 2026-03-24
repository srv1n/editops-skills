import fs from 'fs';
import path from 'path';
import process from 'process';
import puppeteer from 'puppeteer-core';

const parseArgs = () => {
  const args = process.argv.slice(2);
  const out = {};
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    const n = args[i + 1];
    if (a === '--spec-json') {
      out.specJson = n;
      i++;
    } else if (a === '--frames-dir') {
      out.framesDir = n;
      i++;
    } else if (a === '--verbose') {
      out.verbose = true;
    } else if (a === '--help' || a === '-h') {
      out.help = true;
    }
  }
  return out;
};

const printHelp = () => {
  console.log(`MapLibre time-sliced frame renderer

Usage:
  node render_frames.mjs --spec-json <spec.json> --frames-dir <dir>

Spec format:
  {
    "width": 1080,
    "height": 1920,
    "fps": 60,
    "duration_sec": 6.0,
    "style_url": "https://demotiles.maplibre.org/style.json",
    "route_lng_lat": [[2.3522,48.8566],[13.4050,52.5200]],
    "line_color": "#00E5FF",
    "line_width": 8.0,
    "marker_color": "#FFFFFF",
    "zoom": 4.0,
    "pitch": 45.0,
    "bearing": 0.0
  }
`);
};

const resolveChromeExecutable = () => {
  const env = process.env.PUPPETEER_EXECUTABLE_PATH;
  if (env && fs.existsSync(env)) return env;

  const candidates = [
    // macOS
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    // Linux
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium-browser',
    '/usr/bin/chromium',
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return null;
};

const main = async () => {
  const args = parseArgs();
  if (args.help || !args.specJson || !args.framesDir) {
    printHelp();
    process.exit(args.help ? 0 : 2);
  }

  const specPath = path.resolve(args.specJson);
  const framesDir = path.resolve(args.framesDir);
  if (!fs.existsSync(specPath)) {
    console.error(`ERROR: spec json not found: ${specPath}`);
    process.exit(2);
  }

  const spec = JSON.parse(fs.readFileSync(specPath, 'utf-8'));
  const width = Number(spec.width || 1080);
  const height = Number(spec.height || 1920);
  const fps = Number(spec.fps || 60);
  const durationSec = Number(spec.duration_sec || 6);
  const totalFrames = Math.max(1, Math.round(durationSec * fps));

  fs.mkdirSync(framesDir, { recursive: true });

  const executablePath = resolveChromeExecutable();
  if (!executablePath) {
    console.error(
      "ERROR: Could not find a Chrome/Chromium executable for puppeteer-core.\n" +
        "Set PUPPETEER_EXECUTABLE_PATH to an installed browser binary."
    );
    process.exit(2);
  }

  if (args.verbose) {
    console.error(`chrome: ${executablePath}`);
    console.error(`frames: ${totalFrames} @ ${fps}fps`);
  }

  const browser = await puppeteer.launch({
    executablePath,
    headless: 'new',
    args: [
      // WebGL is required for MapLibre. Force a software GL implementation so
      // headless renders work consistently (GPU may be unavailable in CI).
      '--use-angle=swiftshader',
      '--ignore-gpu-blocklist',
      '--enable-webgl',
      '--no-sandbox',
      '--disable-dev-shm-usage',
      '--disable-background-timer-throttling',
      '--disable-backgrounding-occluded-windows',
      '--disable-renderer-backgrounding',
    ],
  });

  try {
    const page = await browser.newPage();
    await page.setViewport({ width, height, deviceScaleFactor: 1 });

    const html = `<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <link href="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css" rel="stylesheet" />
    <style>
      html, body { margin: 0; padding: 0; width: 100%; height: 100%; background: #000; overflow: hidden; }
      #map { position: absolute; inset: 0; }
      canvas { image-rendering: -webkit-optimize-contrast; }
    </style>
  </head>
  <body>
    <div id="map"></div>
    <script src="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js"></script>
    <script>
      window.__clipper_ready = false;
      window.__clipper = null;

      const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

      const haversineMeters = (a, b) => {
        const toRad = (d) => (d * Math.PI) / 180;
        const R = 6371000;
        const dLat = toRad(b[1] - a[1]);
        const dLng = toRad(b[0] - a[0]);
        const lat1 = toRad(a[1]);
        const lat2 = toRad(b[1]);
        const s =
          Math.sin(dLat / 2) * Math.sin(dLat / 2) +
          Math.sin(dLng / 2) * Math.sin(dLng / 2) * Math.cos(lat1) * Math.cos(lat2);
        return 2 * R * Math.atan2(Math.sqrt(s), Math.sqrt(1 - s));
      };

      const buildArc = (route) => {
        const pts = Array.isArray(route) ? route : [];
        const dist = [0];
        for (let i = 1; i < pts.length; i++) {
          dist.push(dist[dist.length - 1] + haversineMeters(pts[i - 1], pts[i]));
        }
        const total = dist[dist.length - 1] || 1;
        return { pts, dist, total };
      };

      const pointAt = (arc, p) => {
        const t = clamp(p, 0, 1) * arc.total;
        for (let i = 1; i < arc.dist.length; i++) {
          if (arc.dist[i] >= t) {
            const prev = arc.dist[i - 1];
            const seg = arc.dist[i] - prev;
            const u = seg <= 0 ? 0 : (t - prev) / seg;
            const a = arc.pts[i - 1];
            const b = arc.pts[i];
            return [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u];
          }
        }
        return arc.pts[arc.pts.length - 1];
      };

      const bbox = (route) => {
        let minLng = Infinity, minLat = Infinity, maxLng = -Infinity, maxLat = -Infinity;
        for (const [lng, lat] of route) {
          minLng = Math.min(minLng, lng);
          minLat = Math.min(minLat, lat);
          maxLng = Math.max(maxLng, lng);
          maxLat = Math.max(maxLat, lat);
        }
        return [[minLng, minLat], [maxLng, maxLat]];
      };

      window.__initClipperMap = async (spec) => {
        const styleUrl = spec.style_url || 'https://demotiles.maplibre.org/style.json';
        const route = spec.route_lng_lat || [];
        if (!Array.isArray(route) || route.length < 2) {
          throw new Error('spec.route_lng_lat must have >= 2 points');
        }
        const arc = buildArc(route);

        const map = new maplibregl.Map({
          container: 'map',
          style: styleUrl,
          interactive: false,
          preserveDrawingBuffer: true,
          attributionControl: false,
        });

        await new Promise((resolve) => map.once('load', resolve));

        map.addSource('route', {
          type: 'geojson',
          data: { type: 'Feature', geometry: { type: 'LineString', coordinates: route } },
          lineMetrics: true,
        });

        map.addSource('marker', {
          type: 'geojson',
          data: { type: 'Feature', geometry: { type: 'Point', coordinates: route[0] } },
        });

        map.addLayer({
          id: 'route-line-glow',
          type: 'line',
          source: 'route',
          layout: { 'line-join': 'round', 'line-cap': 'round' },
          paint: {
            'line-width': (spec.line_width || 8) + 10,
            'line-color': spec.line_color || '#00E5FF',
            'line-opacity': 0.18,
            'line-blur': 6,
            'line-gradient': ['step', ['line-progress'], spec.line_color || '#00E5FF', 0, 'rgba(0,0,0,0)'],
          },
        });

        map.addLayer({
          id: 'route-line',
          type: 'line',
          source: 'route',
          layout: { 'line-join': 'round', 'line-cap': 'round' },
          paint: {
            'line-width': spec.line_width || 8,
            'line-color': spec.line_color || '#00E5FF',
            'line-opacity': 0.98,
            'line-gradient': ['step', ['line-progress'], spec.line_color || '#00E5FF', 0, 'rgba(0,0,0,0)'],
          },
        });

        map.addLayer({
          id: 'marker',
          type: 'circle',
          source: 'marker',
          paint: {
            'circle-radius': (spec.line_width || 8) * 1.25,
            'circle-color': spec.marker_color || '#FFFFFF',
            'circle-opacity': 0.92,
            'circle-stroke-color': '#000000',
            'circle-stroke-opacity': 0.22,
            'circle-stroke-width': 2,
          },
        });

        // Initial fit unless explicit zoom was provided.
        const bb = bbox(route);
        if (spec.zoom === undefined || spec.zoom === null) {
          map.fitBounds(bb, { padding: 120, duration: 0 });
        } else {
          map.jumpTo({ center: route[0], zoom: spec.zoom });
        }

        map.jumpTo({
          pitch: spec.pitch === undefined || spec.pitch === null ? 45 : spec.pitch,
          bearing: spec.bearing === undefined || spec.bearing === null ? 0 : spec.bearing,
        });

        const setProgress = async (p) => {
          const prog = clamp(p, 0, 1);

          const lineColor = spec.line_color || '#00E5FF';
          const transparent = 'rgba(0,0,0,0)';
          const gradient = ['step', ['line-progress'], lineColor, prog, transparent];
          map.setPaintProperty('route-line', 'line-gradient', gradient);
          map.setPaintProperty('route-line-glow', 'line-gradient', gradient);

          const pt = pointAt(arc, prog);
          map.getSource('marker').setData({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: pt },
          });

          // Track camera center on the moving point (jumpTo keeps it deterministic).
          map.jumpTo({ center: pt });

          // Wait for map to be fully rendered and idle (tiles loaded).
          await new Promise((resolve) => {
            let done = false;
            const finish = () => {
              if (done) return;
              done = true;
              resolve(true);
            };
            const onIdle = () => {
              map.off('idle', onIdle);
              finish();
            };
            map.on('idle', onIdle);
            map.triggerRepaint();
            setTimeout(() => {
              map.off('idle', onIdle);
              finish();
            }, 10000);
          });
        };

        // Prime idle once.
        await setProgress(0);

        window.__clipper = { setProgress };
        window.__clipper_ready = true;
      };
    </script>
  </body>
</html>`;

    await page.setContent(html, { waitUntil: 'networkidle0' });

    await page.waitForFunction('window.maplibregl !== undefined', { timeout: 60000 });
    await page.evaluate((s) => window.__initClipperMap(s), spec);
    await page.waitForFunction('window.__clipper_ready === true', { timeout: 60000 });

    for (let i = 0; i < totalFrames; i++) {
      const p = totalFrames <= 1 ? 1 : i / (totalFrames - 1);
      await page.evaluate((prog) => window.__clipper.setProgress(prog), p);
      const file = path.join(framesDir, `frame_${String(i + 1).padStart(6, '0')}.png`);
      await page.screenshot({ path: file, type: 'png' });
      if (args.verbose && (i === 0 || (i + 1) % 60 === 0 || i === totalFrames - 1)) {
        console.error(`frame ${i + 1}/${totalFrames}`);
      }
    }

    if (args.verbose) {
      console.error('ok frames complete');
    }
  } finally {
    await browser.close();
  }
};

main().catch((err) => {
  console.error('ERROR:', err?.stack || String(err));
  process.exit(1);
});
