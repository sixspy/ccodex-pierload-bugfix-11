export const dashboardViewTemplate = `
<section class="panel">
  <h2>闸口总览</h2>
  <p class="hint">过磅票入闸后裁定超重才放行，驳回进旁路挂起；闸代快照须与提单册对账。海关完成只认回执到达后的正式册，影子待回执不计完成。</p>
  <div class="hero-cards">
    <div class="hero-card"><span class="t">地磅桥</span><div class="n">{{ dash.bridges ?? '—' }}</div></div>
    <div class="hero-card"><span class="t">开放超重</span><div class="n">{{ dash.open_overloads ?? '—' }}</div></div>
    <div class="hero-card"><span class="t">旁路挂起</span><div class="n">{{ dash.side_pending ?? '—' }}</div></div>
    <div class="hero-card"><span class="t">过磅票</span><div class="n">{{ dash.tickets ?? '—' }}</div></div>
    <div class="hero-card"><span class="t">已放行</span><div class="n">{{ dash.gate_releases ?? '—' }}</div></div>
    <div class="hero-card"><span class="t">已封闸</span><div class="n">{{ dash.gate_seals ?? '—' }}</div></div>
  </div>
  <div class="hero-cards" style="margin-top:12px">
    <div class="hero-card"><span class="t">海关正式完成</span><div class="n">{{ dash.customs_official ?? '—' }}</div></div>
    <div class="hero-card"><span class="t">影子待回执</span><div class="n">{{ dash.customs_shadow ?? '—' }}</div></div>
    <div class="hero-card"><span class="t">未正式完成</span><div class="n">{{ dash.customs_pending ?? '—' }}</div></div>
  </div>
  <p class="hint" style="margin-top:16px">骨架：{{ dash.meta && dash.meta.skeleton }}</p>
</section>
`;
