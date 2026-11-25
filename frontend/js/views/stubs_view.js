export const stubsViewTemplate = `
<section class="grid-2">
  <section class="panel">
    <h2>海关申报空桩</h2>
    <p class="hint">入队只到「已入队」。回执列为空即不得写正式完成；结算只认回执到达后的原子晋升，影子待回执不得当结案。</p>
    <div class="form-row">
      <div><label>提单</label><input v-model="customsForm.bl_code"/></div>
      <div><label>行数</label><input v-model.number="customsForm.row_count" type="number"/></div>
    </div>
    <div class="actions"><button @click="enqueueCustoms">入队</button></div>
    <div class="form-row" style="margin-top:12px">
      <div><label>回执号（到达后填）</label><input v-model="receiptForm.receipt_ref" placeholder="CUS-..."/></div>
      <div><label>任务 ID</label><input v-model.number="receiptForm.job_id" type="number"/></div>
    </div>
    <div class="actions"><button @click="submitReceipt">登记回执</button></div>
    <table class="data-table compact">
      <thead><tr><th>提单</th><th>状态</th><th>回执</th><th>影子</th><th></th></tr></thead>
      <tbody>
        <tr v-for="j in customsJobs" :key="j.id">
          <td>{{ j.bl_code }}</td>
          <td><span class="badge" :class="customsBadge(j.status)">{{ customsLabel(j.status) }}</span></td>
          <td class="mono">{{ j.receipt_ref || '空' }}</td>
          <td class="mono">{{ j.shadow_completed_at ? '有' : '无' }}</td>
          <td><button class="mini-btn" @click="pickJob(j)">选</button></td>
        </tr>
      </tbody>
    </table>
  </section>
  <section class="panel">
    <h2>岸桥计划空桩</h2>
    <p class="hint">岸桥空桩与海关回执口径分开；本页不参与完成判定。</p>
    <div class="form-row">
      <div><label>泊位</label><input v-model="craneForm.berth_slot"/></div>
      <div><label>票ID(可空)</label><input v-model.number="craneForm.ticket_id" type="number"/></div>
    </div>
    <div class="actions"><button @click="enqueueCrane">入队</button></div>
    <table class="data-table compact">
      <tr v-for="j in craneJobs" :key="j.id"><td>{{ j.berth_slot }}</td><td>{{ j.status }}</td></tr>
    </table>
  </section>
</section>
`;
