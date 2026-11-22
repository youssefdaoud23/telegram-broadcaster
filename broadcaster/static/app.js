"use strict";
document.addEventListener("DOMContentLoaded",()=>{
  for(const form of document.querySelectorAll("form")){
    if(!form.hasAttribute("data-confirm") && !form.querySelector("[data-confirm]")) continue;
    form.addEventListener("submit",(event)=>{
      const prompt=event.submitter?.getAttribute("data-confirm") || form.getAttribute("data-confirm");
      if(prompt && !window.confirm(prompt)) event.preventDefault();
    });
  }
  const message=document.getElementById("compose-message");
  const count=document.getElementById("char-count");
  if(message&&count){
    const refresh=()=>{count.textContent=message.value.length+" / 4096";};
    message.addEventListener("input",refresh);refresh();
  }
  const checkboxes=[...document.querySelectorAll(".recipient-checkbox")];
  const selectAll=document.getElementById("select-all");
  const selection=document.getElementById("selected-count");
  const recipientSearch=document.getElementById("recipient-search");
  const refreshRecipients=()=>{
    if(selection)selection.textContent=checkboxes.filter(x=>x.checked).length+" selected";
    if(selectAll) selectAll.checked=checkboxes.length>0&&checkboxes.every(x=>x.checked);
  };
  for(const c of checkboxes)c.addEventListener("change",refreshRecipients);
  const recipientGroups=[...document.querySelectorAll(".recipient-group-checkbox")];
  for(const groupCheckbox of recipientGroups){
    groupCheckbox.addEventListener("change",()=>{
      const selectedGroups=recipientGroups.filter(box=>box.checked).map(box=>box.value);
      for(const contactCheckbox of checkboxes){
        const row=contactCheckbox.closest(".recipient-row");
        const memberships=row ? (row.getAttribute("data-groups")||"") : "";
        contactCheckbox.checked=selectedGroups.some(groupId=>memberships.includes("|"+groupId+"|"));
      }
      refreshRecipients();
    });
  }
  for(const button of document.querySelectorAll("[data-reuse-preset]")){
    button.addEventListener("click",()=>{
      const preset=button.getAttribute("data-reuse-preset");
      for(const groupCheckbox of recipientGroups)groupCheckbox.checked=false;
      for(const checkbox of checkboxes){
        const previous=checkbox.getAttribute("data-previous-state")||"";
        checkbox.checked=(
          preset==="all" ? !!previous :
          preset==="none" ? false :
          preset==="failed" ? (previous==="failed"||previous==="blocked") :
          preset==="unknown" ? (previous==="unknown"||previous==="sending") :
          previous===preset
        );
      }
      refreshRecipients();
    });
  }
  if(selectAll)selectAll.addEventListener("change",()=>{
    for(const groupCheckbox of recipientGroups)groupCheckbox.checked=false;
    for(const c of checkboxes)c.checked=selectAll.checked;
    refreshRecipients();
  });
  if(recipientSearch)recipientSearch.addEventListener("input",()=>{
    const query=recipientSearch.value.trim().toLowerCase();
    for(const row of document.querySelectorAll(".recipient-row")){
      row.hidden=!row.getAttribute("data-search").includes(query);
    }
  });
  refreshRecipients();
  const bulkContactChecks=[...document.querySelectorAll(".bulk-contact-checkbox")];
  const bulkContactSelectAll=document.getElementById("bulk-select-all");
  const bulkContactCount=document.getElementById("bulk-contact-count");
  const bulkContactForm=document.getElementById("bulk-contact-group-form");
  if(bulkContactChecks.length){
    const refreshBulkContacts=()=>{
      const selected=bulkContactChecks.filter(c=>c.checked).length;
      if(bulkContactCount)bulkContactCount.textContent=selected+" selected";
      if(bulkContactSelectAll)bulkContactSelectAll.checked=selected===bulkContactChecks.length;
      for(const button of document.querySelectorAll(
        '#bulk-contact-group-form button[type="submit"],form[data-bulk-selected] button[type="submit"]'
      )){
        button.disabled=selected===0;
      }
    };
    if(bulkContactSelectAll)bulkContactSelectAll.addEventListener("change",()=>{
      for(const checkbox of bulkContactChecks)checkbox.checked=bulkContactSelectAll.checked;
      refreshBulkContacts();
    });
    for(const checkbox of bulkContactChecks)checkbox.addEventListener("change",refreshBulkContacts);
    for(const form of document.querySelectorAll("form[data-bulk-selected]")){
      form.addEventListener("submit",(event)=>{
        if(event.defaultPrevented)return;
        form.querySelectorAll('input[data-bulk-copy]').forEach(c=>c.remove());
        const selected=bulkContactChecks.filter(c=>c.checked);
        if(!selected.length){
          event.preventDefault();
          return;
        }
        for(const checkbox of selected){
          const copy=document.createElement("input");
          copy.type="hidden";
          copy.name="contacts";
          copy.value=checkbox.value;
          copy.setAttribute("data-bulk-copy","");
          form.appendChild(copy);
        }
      });
    }
    refreshBulkContacts();
  }
  const groupMemberChecks=[...document.querySelectorAll(".group-member-checkbox")];
  const groupMemberSearch=document.getElementById("group-member-search");
  const groupMemberCount=document.getElementById("group-member-count");
  const groupMemberSelectAll=document.getElementById("group-member-select-all");
  if(groupMemberChecks.length||groupMemberSearch){
    const refreshGroupMembers=()=>{
      const selected=groupMemberChecks.filter(c=>c.checked).length;
      if(groupMemberCount)groupMemberCount.textContent=selected+" selected";
      const shown=groupMemberChecks.filter(c=>!c.closest(".group-member-row").hidden);
      if(groupMemberSelectAll)groupMemberSelectAll.checked=shown.length>0&&shown.every(c=>c.checked);
    };
    for(const checkbox of groupMemberChecks)checkbox.addEventListener("change",refreshGroupMembers);
    if(groupMemberSelectAll)groupMemberSelectAll.addEventListener("change",()=>{
      for(const checkbox of groupMemberChecks){
        if(!checkbox.closest(".group-member-row").hidden)checkbox.checked=groupMemberSelectAll.checked;
      }
      refreshGroupMembers();
    });
    if(groupMemberSearch)groupMemberSearch.addEventListener("input",()=>{
      const query=groupMemberSearch.value.trim().toLowerCase();
      for(const row of document.querySelectorAll(".group-member-row")){
        row.hidden=!(row.getAttribute("data-group-member-search")||"").includes(query);
      }
      refreshGroupMembers();
    });
    refreshGroupMembers();
  }
  const groupSearch=document.getElementById("group-search");
  if(groupSearch){
    const groupRows=[...document.querySelectorAll(".group-export-item[data-group-name]")];
    const summary=document.getElementById("group-match-summary");
    const noMatch=document.getElementById("group-no-match");
    const filterGroups=()=>{
      const query=groupSearch.value.trim().toLocaleLowerCase();
      let visible=0;
      for(const row of groupRows){
        const match=row.getAttribute("data-group-name").includes(query);
        row.hidden=!match;
        if(!match){
          const radio=row.querySelector('input[type="radio"]');
          if(radio) radio.checked=false;
        }else visible++;
      }
      if(summary) summary.textContent=visible+" of "+groupRows.length+" groups shown";
      if(noMatch) noMatch.hidden=visible!==0;
    };
    groupSearch.addEventListener("input",filterGroups);
    filterGroups();
  }
  const groupProgress=document.querySelector("[data-group-scan-refresh]");
  if(groupProgress && groupProgress.getAttribute("data-group-scan-refresh")==="true"){
    window.setTimeout(()=>window.location.reload(),3000);
  }
  const refresh=document.getElementById("campaign-refresh");
  if(refresh && refresh.getAttribute("data-active")==="true"){
    window.setTimeout(()=>window.location.reload(),4000);
  }
});
