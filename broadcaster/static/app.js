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
  const contactSearch=document.getElementById("contacts-filter-search");
  const contactGroup=document.getElementById("contacts-filter-group");
  const contactRows=[...document.querySelectorAll(".contact-filter-row")];
  const foldContactSearch=value=>String(value).normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLocaleLowerCase();
  // Index once; large contact lists must not reparse every row on each key.
  const indexedContacts=contactRows.map(row=>{
    const text=foldContactSearch((row.getAttribute("data-contact-search")||"")+" "+row.textContent);
    return {row,text,digits:text.replace(/[^0-9]/g,""),
      groupIds:row.getAttribute("data-contact-groups")||"",
      checkbox:row.querySelector(".bulk-contact-checkbox")};
  });
  const contactResultCount=document.getElementById("contact-filter-count");
  const contactNoResults=document.getElementById("contacts-filter-empty");
  const contactFilterForm=document.querySelector(".contact-filter-bar");
  const contactFilterSubmit=document.getElementById("contacts-filter-submit");
  if(contactFilterSubmit)contactFilterSubmit.hidden=true;

  const visibleContactChecks=()=>bulkContactChecks.filter(
    c=>!c.closest(".contact-filter-row").hidden
  );
  const refreshBulkContacts=()=>{
    const selected=bulkContactChecks.filter(c=>c.checked).length;
    const shown=visibleContactChecks();
    const shownSelected=shown.filter(c=>c.checked).length;
    if(bulkContactCount)bulkContactCount.textContent=selected+" selected";
    if(bulkContactSelectAll){
      bulkContactSelectAll.checked=shown.length>0&&shownSelected===shown.length;
      bulkContactSelectAll.indeterminate=shownSelected>0&&shownSelected<shown.length;
    }
    for(const button of document.querySelectorAll(
      '#bulk-contact-group-form button[type="submit"],form[data-bulk-selected] button[type="submit"]'
    )){
      button.disabled=selected===0;
    }
  };
  const filterContacts=()=>{
    const needle=contactSearch ? foldContactSearch(contactSearch.value.trim()) : "";
    const digits=needle.replace(/[^0-9]/g,"");
    const group=contactGroup ? contactGroup.value : "0";
    let count=0;
    for(const item of indexedContacts){
      const matchText=(!needle||item.text.includes(needle)||
        (digits.length>=3&&item.digits.includes(digits)));
      const matchGroup=group==="0"||item.groupIds.includes("|"+group+"|");
      item.row.hidden=!(matchText&&matchGroup);
      if(!item.row.hidden)count++;
      else if(item.checkbox)item.checkbox.checked=false; // Never bulk-edit filtered-out contacts.
    }
    if(contactResultCount)contactResultCount.textContent=count+" of "+contactRows.length+" shown";
    if(contactNoResults)contactNoResults.hidden=count!==0||contactRows.length===0;
    refreshBulkContacts();
  };
  if(contactFilterForm)contactFilterForm.addEventListener("submit",event=>{
    event.preventDefault();
    filterContacts();
  });
  if(contactSearch)contactSearch.addEventListener("input",filterContacts);
  if(contactGroup)contactGroup.addEventListener("change",filterContacts);
  if(bulkContactSelectAll)bulkContactSelectAll.addEventListener("change",()=>{
    for(const box of visibleContactChecks())box.checked=bulkContactSelectAll.checked;
    refreshBulkContacts();
  });
  for(const checkbox of bulkContactChecks)checkbox.addEventListener("change",refreshBulkContacts);
  for(const form of document.querySelectorAll("form[data-bulk-selected]")){
    form.addEventListener("submit",event=>{
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
  if(contactSearch||contactGroup)filterContacts();
  else refreshBulkContacts();
  // A group page shows its actual members. Independent Add and Remove
  // pickers use checkboxes for actions; never replace all group membership.
  for(const form of document.querySelectorAll("form[data-group-picker]")){
    const search=form.querySelector(".group-picker-search");
    const selectAll=form.querySelector(".group-picker-select-all");
    const count=form.querySelector(".group-picker-selected-count");
    const submit=form.querySelector(".group-picker-submit");
    const noResults=form.querySelector(".group-picker-no-results");
    const indexed=[...form.querySelectorAll(".group-member-row")].map(row=>({
      row,
      search:((row.getAttribute("data-group-member-search")||"")+" "+row.textContent)
        .normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLocaleLowerCase(),
      checkbox:row.querySelector(".group-picker-checkbox")
    }));
    const visible=()=>indexed.filter(item=>!item.row.hidden);
    const refresh=()=>{
      const shown=visible();
      const selected=indexed.filter(item=>item.checkbox&&item.checkbox.checked);
      const shownSelected=shown.filter(item=>item.checkbox&&item.checkbox.checked).length;
      if(count)count.textContent=selected.length+" selected";
      if(submit)submit.disabled=selected.length===0;
      if(selectAll){
        selectAll.checked=shown.length>0&&shownSelected===shown.length;
        selectAll.indeterminate=shownSelected>0&&shownSelected<shown.length;
      }
    };
    const filter=()=>{
      const term=(search?.value||"").trim().normalize("NFD")
        .replace(/[\u0300-\u036f]/g,"").toLocaleLowerCase();
      const numbers=term.replace(/[^0-9]/g,"");
      let found=0;
      for(const item of indexed){
        const match=!term||item.search.includes(term)||
          (numbers.length>=3&&item.search.replace(/[^0-9]/g,"").includes(numbers));
        item.row.hidden=!match;
        if(match)found++;
        else if(item.checkbox)item.checkbox.checked=false;
      }
      if(noResults)noResults.hidden=found!==0;
      refresh();
    };
    if(search)search.addEventListener("input",filter);
    if(selectAll)selectAll.addEventListener("change",()=>{
      for(const item of visible())if(item.checkbox)item.checkbox.checked=selectAll.checked;
      refresh();
    });
    for(const item of indexed){
      if(item.checkbox)item.checkbox.addEventListener("change",refresh);
    }
    form.addEventListener("submit",event=>{
      if(!indexed.some(item=>item.checkbox&&item.checkbox.checked)){
        event.preventDefault();
      }
    });
    filter();
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
