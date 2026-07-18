<script lang="ts">
  import type { KnowledgeGraph, EntityType } from "../../types";

  let { graph }: { graph: KnowledgeGraph } = $props();

  const COLOR_MAP: Record<string, string> = {
    chapter: "#8b5cf6", concept: "#10b981", tool: "#f59e0b",
    method: "#3b82f6", technology: "#6366f1", workflow: "#ec4899",
    person: "#ef4444", organization: "#f97316", library: "#06b6d4",
    problem: "#ef4444", solution: "#10b981",
  };
  const LABEL_MAP: Record<string, string> = {
    uses: "uses", depends_on: "depends on", part_of: "part of",
    implements: "implements", improves: "improves", generates: "generates",
    imports: "imports", exports: "exports", related_to: "related",
    similar_to: "similar", conflicts_with: "conflicts",
    requires: "requires", produces: "produces", consumes: "consumes",
  };

  // ── All state is plain variables — no $state anywhere ──
  let graphData: { entities: any[]; relations: any[]; chapters: any[] } | null = null;
  let nodes: any[] = [];
  let edges: any[] = [];
  let selectedId: string | null = null;
  let hoveredId: string | null = null;
  let filterText = '';

  let svgEl: SVGSVGElement;
  let graphContent: SVGGElement;
  let tooltipEl: HTMLDivElement;
  let ttLabelEl: HTMLElement;
  let ttTypeEl: HTMLElement;
  let ttDegreeEl: HTMLElement;
  let ttSummaryEl: HTMLElement;
  let ttRelationsEl: HTMLElement;
  let filterInputEl: HTMLInputElement;
  let filterInfoEl: HTMLElement;

  let transform = { x: 0, y: 0, scale: 1 };
  let dragNodeId: string | null = null;

  const NS = 'http://www.w3.org/2000/svg';

  function nodeColor(et: string) { return COLOR_MAP[et] || "#94a3b8"; }
  function relLabel(rt: string) { return LABEL_MAP[rt] || rt; }
  function slug(text: string) { return text.toLowerCase().replace(/[^a-z0-9\-\_一-鿿]/g, "-").replace(/-+/g, "-"); }

  // ── Build SVG graph entirely via DOM ─────────────────────
  function buildGraphDOM() {
    stopSim();  // stop any running simulation before clearing DOM
    graphContent.replaceChildren();

    const entities = graph.entities as any[];
    const relations = graph.relations as any[];
    const chapters = graph.chapters as any[];

    // Compute degrees
    const degMap = new Map<string, number>();
    for (const r of relations) {
      degMap.set(r.source, (degMap.get(r.source) || 0) + 1);
      degMap.set(r.target, (degMap.get(r.target) || 0) + 1);
    }
    for (const ch of chapters) {
      const chId = `_ch:${slug(ch.title)}`;
      for (const eid of ch.entityIds) {
        degMap.set(chId, (degMap.get(chId) || 0) + 1);
        degMap.set(eid, (degMap.get(eid) || 0) + 1);
      }
    }

    const rect = svgEl.getBoundingClientRect();
    const viewSize = Math.max(rect.width, rect.height, 400);
    const spread = Math.min(Math.max(entities.length * 1.5, 50), viewSize * 0.12);
    const nodeMap = new Map<string, any>();
    nodes = [];
    edges = [];

    // Build nodes with positions — evenly spaced around circle
    const entityCount = entities.length;
    entities.forEach((e: any, i: number) => {
      const deg = degMap.get(e.id) || 0;
      const angle = entityCount > 1 ? (2 * Math.PI * i) / entityCount : 0;
      const n = {
        id: e.id, label: e.name, type: e.entityType,
        summary: e.summary, degree: deg,
        aliases: e.aliases || [], sourceRefs: e.sourceRefs || [],
        color: nodeColor(e.entityType),
        radius: Math.max(6, Math.min(24, 6 + deg * 1.5)),
        x: Math.cos(angle) * spread + Math.random() * 8 - 4,
        y: Math.sin(angle) * spread + Math.random() * 8 - 4,
        vx: 0, vy: 0, pinned: false,
      };
      nodeMap.set(n.id, n);
      nodes.push(n);
    });

    // Chapter nodes — placed between entity positions at ~70% radius
    const totalNodes = entityCount + chapters.length;
    chapters.forEach((ch: any, ci: number) => {
      const chId = `_ch:${slug(ch.title)}`;
      const deg = degMap.get(chId) || 0;
      const angle = totalNodes > 1 ? (2 * Math.PI * (entityCount + ci + 0.3)) / totalNodes : 0;
      const n = {
        id: chId, label: ch.title, type: 'chapter',
        summary: '', degree: deg,
        color: nodeColor('chapter'),
        radius: Math.max(8, Math.min(28, 8 + deg * 1.2)),
        x: Math.cos(angle) * spread * 0.7 + Math.random() * 6 - 3,
        y: Math.sin(angle) * spread * 0.7 + Math.random() * 6 - 3,
        vx: 0, vy: 0, pinned: false,
      };
      nodeMap.set(n.id, n);
      nodes.push(n);
      for (const eid of ch.entityIds) {
        edges.push({ source: n.id, target: eid, label: 'contains' });
      }
    });

    // Edges
    relations.forEach((r: any) => {
      edges.push({ source: r.source, target: r.target, label: relLabel(r.relationType) });
    });

    // Draw edges (lines first, so they're behind nodes)
    const edgeData: any[] = [];
    edges.forEach((e: any) => {
      const s = nodeMap.get(e.source);
      const t = nodeMap.get(e.target);
      if (!s || !t) return;
      edgeData.push({ e, s, t });
    });

    // Create edge elements
    edgeData.forEach(({ e, s, t }) => {
      const line = document.createElementNS(NS, 'line');
      line.setAttribute('x1', String(s.x));
      line.setAttribute('y1', String(s.y));
      line.setAttribute('x2', String(t.x));
      line.setAttribute('y2', String(t.y));
      line.setAttribute('stroke', 'var(--border-color)');
      line.setAttribute('stroke-width', '1.5');
      graphContent.appendChild(line);

      const txt = document.createElementNS(NS, 'text');
      txt.setAttribute('x', String((s.x + t.x) / 2));
      txt.setAttribute('y', String((s.y + t.y) / 2 - 5));
      txt.setAttribute('text-anchor', 'middle');
      txt.setAttribute('fill', 'var(--text-tertiary)');
      txt.setAttribute('font-size', '8');
      txt.textContent = e.label;
      graphContent.appendChild(txt);
    });

    // Create node elements
    nodes.forEach((n: any) => {
      const g = document.createElementNS(NS, 'g');
      g.setAttribute('class', 'node-group');
      g.setAttribute('data-nid', n.id);
      g.setAttribute('transform', `translate(${n.x},${n.y})`);
      g.setAttribute('role', 'button');
      g.setAttribute('tabindex', '0');
      g.style.cursor = 'pointer';

      g.addEventListener('pointerenter', () => { hoveredId = n.id; updateHighlights(); });
      g.addEventListener('pointerleave', () => { hoveredId = null; updateHighlights(); });
      g.addEventListener('pointerdown', (e: PointerEvent) => nodePointerDown(e, n.id));

      const circle = document.createElementNS(NS, 'circle');
      circle.setAttribute('r', String(n.radius));
      circle.setAttribute('fill', n.color);
      circle.setAttribute('stroke', '#fff');
      circle.setAttribute('stroke-width', '2');
      g.appendChild(circle);

      const label = document.createElementNS(NS, 'text');
      label.setAttribute('y', String(n.radius + 10));
      label.setAttribute('text-anchor', 'middle');
      label.setAttribute('fill', 'var(--text-primary)');
      label.setAttribute('font-size', '10');
      label.textContent = n.label.length > 16 ? n.label.slice(0, 15) + '...' : n.label;
      g.appendChild(label);

      graphContent.appendChild(g);
    });

    // Center graph - initially at viewport center
    if (rect.width > 0) {
      transform = { x: rect.width / 2, y: rect.height / 2, scale: 1 };
      graphContent.setAttribute('transform', `translate(${transform.x},${transform.y}) scale(${transform.scale})`);
    }

    // Force all node positions NOW (synchronous set)
    syncNodePositions();

    // Start simulation
    setTimeout(startSim, 200);
  }

  // ── Force simulation (direct DOM) ───────────────────────
  let simTimer: ReturnType<typeof setInterval> | null = null;
  function stopSim() { if (simTimer) { clearInterval(simTimer); simTimer = null; } }

        function simulate(frame = 999) {
    const n = nodes.length;
    if (n === 0) return;
    const moving = dragNodeId !== null;
    const ramp = moving ? Math.min(1, frame / 40) : 1;
    const repulsion = 15 * ramp;
    const attraction = 0.004;
    const gravity = 0.0002;
    const damping = moving ? 0.96 : 0.99;
    const now = Date.now();

    // Subtle all-pairs repulsion (always on, very gentle)
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const a = nodes[i], b = nodes[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let dist = Math.sqrt(dx * dx + dy * dy) || 1;
        if (dist > 80) continue;
        let force = repulsion / (dist + 20);
        const fx = dx / dist * force, fy = dy / dist * force;
        if (!a.pinned) { a.vx += fx; a.vy += fy; }
        if (!b.pinned) { b.vx -= fx; b.vy -= fy; }
      }
    }
    // Edge attraction
    for (const e of edges) {
      const s = nodes.find((nd: any) => nd.id === e.source);
      const t = nodes.find((nd: any) => nd.id === e.target);
      if (!s || !t) continue;
      const dx = t.x - s.x, dy = t.y - s.y;
      const fx = dx * attraction, fy = dy * attraction;
      if (!s.pinned) { s.vx += fx; s.vy += fy; }
      if (!t.pinned) { t.vx -= fx; t.vy -= fy; }
    }
    for (const nd of nodes) {
      if (nd.pinned) continue;
      nd.vx += (0 - nd.x) * gravity;
      nd.vy += (0 - nd.y) * gravity;
      // Gentle breathing wave
      const breath = Math.sin(now * 0.0015 + nd.x * 0.008 + nd.y * 0.008) * 0.004;
      nd.vx += breath; nd.vy += breath;
      nd.vx *= damping; nd.vy *= damping;
      nd.x += nd.vx; nd.y += nd.vy;
    }
    syncNodePositions();
  }

  function syncNodePositions() {
    if (!graphContent) return;
    const groups = graphContent.querySelectorAll('.node-group');
    const lines = graphContent.querySelectorAll('line');
    const texts = graphContent.querySelectorAll('text');

    for (const g of groups) {
      const nid = g.getAttribute('data-nid');
      const n = nodes.find((nd: any) => nd.id === nid);
      if (n) g.setAttribute('transform', `translate(${n.x},${n.y})`);
    }
    for (let i = 0; i < edges.length; i++) {
      if (i >= lines.length) break;
      const e = edges[i];
      const s = nodes.find((nd: any) => nd.id === e.source);
      const t = nodes.find((nd: any) => nd.id === e.target);
      if (!s || !t) continue;
      lines[i].setAttribute('x1', String(s.x));
      lines[i].setAttribute('y1', String(s.y));
      lines[i].setAttribute('x2', String(t.x));
      lines[i].setAttribute('y2', String(t.y));
      if (texts[i]) {
        texts[i].setAttribute('x', String((s.x + t.x) / 2));
        texts[i].setAttribute('y', String((s.y + t.y) / 2 - 5));
      }
    }
  }

  function startSim() {
    if (nodes.length === 0) return;
    stopSim();
    let frame = 0;
    simTimer = setInterval(() => {
      frame++;
      simulate(frame);
    }, 40);
  }

  // ── UI updates (direct DOM) ─────────────────────────────
  function getActiveId() { return selectedId || (hoveredId && (!selectedId || hoveredId !== selectedId) ? hoveredId : null) || null; }

  function updateUI() {
    const activeId = selectedId || hoveredId || null;
    const nodeData = activeId ? nodes.find((n: any) => n.id === activeId) : null;

    // Visual glow on selected node (Obsidian style) — no floating box
    tooltipEl.style.display = 'none';
    const allCircles = graphContent?.querySelectorAll('circle');
    if (allCircles) {
      for (const c of allCircles) {
        const parent = c.closest('[data-nid]') as SVGElement | null;
        const nid = parent?.getAttribute('data-nid');
        if (nid === selectedId) {
          c.setAttribute('stroke', nodeData?.color || '#fff');
          c.setAttribute('stroke-width', '3.5');
          c.setAttribute('filter', 'drop-shadow(0 0 6px ' + (nodeData?.color || '#fff') + ')');
        } else if (nid === hoveredId && nid !== selectedId) {
          c.setAttribute('stroke', '#fff');
          c.setAttribute('stroke-width', '2.5');
          c.removeAttribute('filter');
        } else {
          c.setAttribute('stroke', '#fff');
          c.setAttribute('stroke-width', '2');
          c.removeAttribute('filter');
        }
      }
    }

    // Edge glow on selected node connections
    const allLines = graphContent?.querySelectorAll('line');
    if (allLines && selectedId) {
      for (const line of allLines) {
        const x1 = line.getAttribute('x1');
        const y1 = line.getAttribute('y1');
        // Simple check: connected to selected node if edge endpoints are near the dragged node
        // We'll just glow the direct edges via opacity/color
        for (const e of edges) {
          if (e.source === selectedId || e.target === selectedId) {
            line.setAttribute('stroke', 'var(--accent-color)');
            line.setAttribute('stroke-width', '2');
            line.style.filter = 'brightness(1.3)';
          }
        }
      }
    }

    updateHighlights();
  }

  function updateHighlights() {
    const activeId = getActiveId();
    const groups = graphContent?.querySelectorAll('.node-group');
    const lines = graphContent?.querySelectorAll('line');
    const texts = graphContent?.querySelectorAll('text');
    if (!groups) return;

    let direct = new Set<string>();
    let indirect = new Set<string>();
    let hidden = new Set<string>();

    if (activeId) {
      direct.add(activeId);
      for (const e of edges) {
        if (e.source === activeId) direct.add(e.target);
        if (e.target === activeId) direct.add(e.source);
      }
      for (const e of edges) {
        if (direct.has(e.source) && !direct.has(e.target)) indirect.add(e.target);
        if (direct.has(e.target) && !direct.has(e.source)) indirect.add(e.source);
      }
    }

    const hasFilter = filterText.length >= 1;
    let matchCount = 0;
    if (hasFilter) {
      const q = filterText.toLowerCase();
      for (const n of nodes) {
        if (n.label.toLowerCase().includes(q)) matchCount++;
        else hidden.add(n.id);
      }
    }

    // Nodes
    for (const g of groups) {
      const nid = g.getAttribute('data-nid');
      if (!nid) continue;
      let op = 1;
      if (hidden.has(nid)) op = 0.06;
      else if (activeId) {
        if (direct.has(nid)) op = 1;
        else if (indirect.has(nid)) op = 0.5;
        else op = 0.15;
      }
      g.setAttribute('opacity', String(op));
    }

    // Edges
    for (let i = 0; i < edges.length; i++) {
      const e = edges[i];
      let op = 0.25;
      if (hasFilter && (hidden.has(e.source) || hidden.has(e.target))) {
        op = 0.02;
      } else if (activeId) {
        const sDir = direct.has(e.source), tDir = direct.has(e.target);
        if (sDir && tDir) op = 0.7;
        else if (sDir || tDir) op = 0.35;
        else op = 0.06;
      }
      if (lines?.[i]) lines[i].setAttribute('opacity', String(op));
      if (texts?.[i]) texts[i].setAttribute('opacity', String(op * 0.8));
    }

    // Filter info
    if (filterInfoEl && hasFilter) {
      filterInfoEl.textContent = `${matchCount}/${nodes.length}`;
      filterInfoEl.style.display = '';
    } else if (filterInfoEl) filterInfoEl.style.display = 'none';
  }

  // ── Interactions ─────────────────────────────────────────
  // RAF render loop for smooth drag animation
  let dragRAF: number | null = null;
  function startDragRender() {
    if (dragRAF) return;
    function render() {
      if (!dragNodeId) { dragRAF = null; return; }
      syncNodePositions();
      dragRAF = requestAnimationFrame(render);
    }
    dragRAF = requestAnimationFrame(render);
  }
  function stopDragRender() {
    if (dragRAF !== null) { cancelAnimationFrame(dragRAF); dragRAF = null; }
  }

  function nodePointerDown(e: PointerEvent, id: string) {
    e.stopPropagation();
    try { const k="vn-debug"; const d=JSON.parse(localStorage.getItem(k)||"[]"); d.push(Date.now()+": PDOWN id="+id); if(d.length>20) d.splice(0,d.length-20); localStorage.setItem(k,JSON.stringify(d)); } catch(e){}
    const n = nodes.find((nd: any) => nd.id === id);
    if (!n) return;
    // DON'T stop sim — let edge physics pull connected nodes during drag (Obsidian behavior)
    // Unpin previous selected node
    if (selectedId && selectedId !== id) {
      const prev = nodes.find((nd: any) => nd.id === selectedId);
      if (prev) prev.pinned = false;
    }
    selectedId = selectedId === id ? null : id;
    n.pinned = true;
    n.vx = 0; n.vy = 0; // zero velocity to prevent wobble on grab
    dragNodeId = id;

    const rect = svgEl.getBoundingClientRect();
    const start = { x: n.x, y: n.y, mx: e.clientX - rect.left, my: e.clientY - rect.top };

    // RAF loop for smooth DOM sync during drag
    startDragRender();

    // Status: show sim is active

    const dragOffsets = new Map();
    // First-degree neighbors
    const neighbors = new Set<string>();
    for (const e of edges) {
      const oid = e.source === id ? e.target : e.source;
      if (e.source !== id && e.target !== id) continue;
      const other = nodes.find((nd) => nd.id === oid);
      if (!other) continue;
      dragOffsets.set(oid, { ox: other.x - n.x, oy: other.y - n.y });
      neighbors.add(oid);
    }
    // Second-degree neighbors (connected through first-degree)
    for (const nid of neighbors) {
      for (const e of edges) {
        const oid = e.source === nid ? e.target : e.source;
        if (e.source !== nid && e.target !== nid) continue;
        if (oid === id || neighbors.has(oid) || dragOffsets.has(oid)) continue;
        const other = nodes.find((nd) => nd.id === oid);
        if (!other) continue;
        const first = nodes.find((nd) => nd.id === nid);
        if (!first) continue;
        dragOffsets.set(oid, { ox: other.x - first.x, oy: other.y - first.y });
      }
    }

    const move = (ev: PointerEvent) => {
      const r2 = svgEl.getBoundingClientRect();
      const node = nodes.find((nd: any) => nd.id === id);
      if (!node) return;
      node.x = start.x + (ev.clientX - r2.left - start.mx) / transform.scale;
      node.y = start.y + (ev.clientY - r2.top - start.my) / transform.scale;
      // Push connected nodes via the edge spring
      const push = 0.01;
      const pushed = new Set<string>();
      for (const e of edges) {
        const oid = e.source === id ? e.target : e.source;
        if (e.source !== id && e.target !== id) continue;
        const off = dragOffsets.get(oid) || { ox: 0, oy: 0 };
        const other = nodes.find((nd: any) => nd.id === oid);
        if (!other || other.pinned) continue;
        const tx = node.x + off.ox, ty = node.y + off.oy;
        other.vx += (tx - other.x) * push;
        other.vy += (ty - other.y) * push;
        other.x += (tx - other.x) * push * 1.5;
        other.y += (ty - other.y) * push * 1.5;
        pushed.add(oid);
      }
      // Push second-degree neighbors (gentler via first-degree)
      for (const nid of neighbors) {
        if (pushed.has(nid)) continue;
        const first = nodes.find((nd: any) => nd.id === nid);
        if (!first || first.pinned) continue;
        const off = dragOffsets.get(nid) || { ox: 0, oy: 0 };
        const tx = first.x + off.ox, ty = first.y + off.oy;
        first.vx += (tx - first.x) * push * 0.5;
        first.vy += (ty - first.y) * push * 0.5;
        first.x += (tx - first.x) * push;
        first.y += (ty - first.y) * push;
        pushed.add(nid);
      }
      syncNodePositions();
    };
    const up = () => {
      dragNodeId = null;
      stopDragRender();
      syncNodePositions();
      const node = nodes.find((nd: any) => nd.id === id);
      // Don't pin — let sim pull it back naturally
      if (node) node.pinned = false;
      // Give a velocity kick toward connected cluster for visible restore
      for (const e of edges) {
        const oid = e.source === id ? e.target : e.source;
        if (e.source !== id && e.target !== id) continue;
        const other = nodes.find(nd => nd.id === oid);
        if (!other) continue;
        node.vx += (other.x - node.x) * 0.005;
        node.vy += (other.y - node.y) * 0.005;
      }
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      syncNodePositions();
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);

    updateUI();
  }

  function navigateToNode(id: string) {
    selectedId = id;
    updateUI();
    const node = nodes.find((n: any) => n.id === id);
    if (node && svgEl) {
      const rect = svgEl.getBoundingClientRect();
      transform = { x: rect.width / 2 - node.x * transform.scale, y: rect.height / 2 - node.y * transform.scale, scale: transform.scale };
      graphContent?.setAttribute('transform', `translate(${transform.x},${transform.y}) scale(${transform.scale})`);
    }
  }

  function handleBackgroundClick() {
    selectedId = null;
    updateUI();
  }

  function handleWheel(e: WheelEvent) {
    e.preventDefault();
    const delta = e.deltaY > 0 ? 0.9 : 1.1;
    const ns = Math.min(4, Math.max(0.2, transform.scale * delta));
    const rect = svgEl.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    transform = { scale: ns, x: mx - (mx - transform.x) * (ns / transform.scale), y: my - (my - transform.y) * (ns / transform.scale) };
    graphContent?.setAttribute('transform', `translate(${transform.x},${transform.y}) scale(${transform.scale})`);
  }

  let panStart: { x: number; y: number; px: number; py: number } | null = null;
  let pointerIdForCapture: number | null = null;
  function handlePanStart(e: PointerEvent) {
    const t = e.target as SVGElement;
    if (t?.closest?.('.node-group')) return;
    if (pointerIdForCapture !== null) { try { svgEl.releasePointerCapture(pointerIdForCapture); } catch {} }
    panStart = { x: e.clientX, y: e.clientY, px: transform.x, py: transform.y };
    pointerIdForCapture = e.pointerId;
    svgEl.setPointerCapture(e.pointerId);
  }
  function handlePanMove(e: PointerEvent) {
    if (!panStart) return;
    transform = { ...transform, x: panStart.px + (e.clientX - panStart.x), y: panStart.py + (e.clientY - panStart.y) };
    graphContent?.setAttribute('transform', `translate(${transform.x},${transform.y}) scale(${transform.scale})`);
  }
  function handlePanEnd() {
    panStart = null;
    if (pointerIdForCapture !== null) { try { svgEl.releasePointerCapture(pointerIdForCapture); } catch {} pointerIdForCapture = null; }
  }

  function handleFilterInput() {
    filterText = filterInputEl?.value || '';
    updateHighlights();
  }

  // ── Init ─────────────────────────────────────────────────
  let lastGraph: any = null;
  function checkGraph() {
    if (!graphContent || !graph || graph === lastGraph || !graph?.entities?.length) return false;
    lastGraph = graph;
    buildGraphDOM();
    return true;
  }

  // Try immediately and poll if needed
  function startInit() {
    if (checkGraph()) return;
    let tries = 0;
    const t = setInterval(() => {
      tries++;
      if (checkGraph() || tries > 80) { clearInterval(t); }
    }, 100);
  }

  $effect(() => {
    if (graph?.entities?.length) {
      startInit();
      // Also try directly after DOM is guaranteed ready
      setTimeout(() => { if (!graphContent?.childNodes?.length) buildGraphDOM(); }, 500);
    }
  });
</script>

<div class="graph-wrap">
  <div class="gt-bar">
    <div class="gt-filter">
      <input bind:this={filterInputEl} class="gt-input" type="text" placeholder="Filter nodes..." oninput={handleFilterInput} onkeydown={(e) => { if (e.key === 'Escape') { filterInputEl.value = ''; handleFilterInput(); } }} />
      <span bind:this={filterInfoEl} class="gt-info"></span>
    </div>
  </div>

  <svg bind:this={svgEl} class="graph-svg" role="img" aria-label="knowledge graph"
    onwheel={handleWheel}
    onpointerdown={handlePanStart} onpointermove={handlePanMove} onpointerup={handlePanEnd} onpointerleave={handlePanEnd}
  >
    <rect width="100%" height="100%" fill="transparent" pointer-events="all" onclick={handleBackgroundClick} onkeydown={(e) => e.key === 'Enter' && handleBackgroundClick()} role="button" tabindex="-1" />
    <g bind:this={graphContent}></g>
  </svg>

  <!-- Tooltip -->
  <div class="tooltip" bind:this={tooltipEl} style="display:none" role="status" aria-live="polite" onpointerdown={(e) => e.stopPropagation()}>
    <div class="tt-head"><span class="tt-dot"></span><strong bind:this={ttLabelEl} class="tt-label">-</strong></div>
    <div class="tt-meta">
      <span bind:this={ttTypeEl} class="tt-type"></span>
      <span bind:this={ttDegreeEl} class="tt-degree"></span>
    </div>
    <p bind:this={ttSummaryEl} class="tt-summary" style="display:none"></p>
    <div bind:this={ttRelationsEl} class="tt-relations"></div>
    <button class="tt-close" onclick={() => { selectedId = null; updateUI(); }}>close</button>
  </div>
</div>

<style>
  .graph-wrap { position:relative; width:100%; min-height:360px; height:100%; overflow:hidden; border-radius:8px; display:flex; flex-direction:column; }
  .gt-bar { display:flex; align-items:center; gap:8px; padding:6px 8px; border-bottom:1px solid var(--border-color); background:var(--bg-card); flex:0 0 auto; }
  .gt-filter { flex:1; display:flex; align-items:center; gap:4px; }
  .gt-input { width:100%; max-width:200px; padding:3px 8px; border:1px solid var(--border-color); border-radius:6px; background:var(--bg-input); color:var(--text-primary); font-size:11px; outline:none; }
  .gt-input:focus { border-color:var(--accent-color); }
  .gt-input::placeholder { color:var(--text-tertiary); }
  .gt-info { font-size:9px; color:var(--text-tertiary); white-space:nowrap; }
  .graph-svg { flex:1; width:100%; min-height:0; display:block; user-select:none; touch-action:none; }
  .tooltip { position:absolute; bottom:14px; left:14px; padding:8px 12px; border:1px solid var(--accent-faint); border-radius:8px; background:var(--bg-elevated); box-shadow:var(--shadow-sm); z-index:100; max-width:280px; }
  .tt-head { display:flex; align-items:center; gap:6px; }
  .tt-dot { width:8px; height:8px; border-radius:50%; flex:0 0 auto; }
  .tt-label { font-size:12px; }
  .tt-meta { display:flex; gap:8px; margin-bottom:4px; }
  .tt-type { font-size:10px; color:var(--text-tertiary); }
  .tt-degree { font-size:10px; color:var(--text-secondary); }
  .tt-summary { font-size:11px; color:var(--text-secondary); line-height:1.4; margin-bottom:4px; }
  .tt-relations { display:flex; flex-wrap:wrap; gap:4px; margin-bottom:2px; }
  .tt-close { border:0; background:transparent; color:var(--text-tertiary); cursor:pointer; font-size:10px; padding:0; }
</style>
