export const reconcileViewTemplate = `
<section class="grid-2">
  <section class="panel">
    <h2>提单对账</h2>
    <p class="hint">汇总过磅毛重与提单申报吨比对。账实平不等于结案：回执未到只能影子，不得当结案。</p>
    <div class="form-row">
      <div><label>提单号</label><input v-model="reconCode"/></div>
    </div>
    <div class="actions">
      <button @click="loadReconcile">对账</button>
      <button class="secondary" @click="loadAllReconcile">全量</button>
    </div>
    <div v-if="reconOne" class="detail-box">
      <p>申报 {{ reconOne.declared_gross_t }}t · 过磅合计 {{ reconOne.ticket_sum_t }}t</p>
      <p :class="reconOne.balanced ? 'ok' : 'bad'">{{ reconOne.balanced ? '账实平衡' : '偏差待查' }}</p>
      <p>海关回执
        <span class="badge" :class="customsBadge(reconOne.customs && reconOne.customs.status)">
          {{ customsLabel(reconOne.customs && reconOne.customs.status) }}
        </span>
        · 回执号 {{ (reconOne.customs && reconOne.customs.receipt_ref) || '空' }}
      </p>
      <p>快照数 {{ reconOne.seal_count }} · 可出证明 {{ reconOne.proof_ready ? '是' : '否' }} · 可结案 {{ reconOne.settled ? '是' : '否' }}</p>
      <p class="hint">{{ reconOne.reason }}</p>
    </div>
    <ul v-if="reconAllItems.length">
      <li v-for="r in reconAllItems" :key="r.bl_code">{{ r.bl_code }} · {{ r.balanced ? '平' : '偏' }} · {{ customsLabel(r.customs && r.customs.status) }}</li>
    </ul>
  </section>
  <section class="panel">
    <h2>单票证明</h2>
    <div class="form-row"><div><label>票号</label><input v-model="proofCode"/></div></div>
    <div class="actions"><button @click="loadProof">拉证明</button></div>
    <div v-if="proofOne" class="detail-box">
      <p>提单匹配 {{ proofOne.manifest_matches_bl ? '是' : '否' }}</p>
      <p>超重闭环 {{ proofOne.overload_closed ? '是' : '否' }}</p>
      <p>已放行 {{ proofOne.released ? '是' : '否' }}</p>
      <p>海关回执
        <span class="badge" :class="customsBadge(proofOne.customs_status)">
          {{ customsLabel(proofOne.customs_status) }}
        </span>
      </p>
      <p>一致 {{ proofOne.coherent ? '是' : '否' }}</p>
    </div>
  </section>
</section>
`;
