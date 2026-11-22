"use strict";
document.addEventListener("DOMContentLoaded",()=>{
  for(const form of document.querySelectorAll("form[data-confirm]")){
    form.addEventListener("submit",(event)=>{
      if(!window.confirm(form.getAttribute("data-confirm"))) event.preventDefault();
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
  for(const button of document.querySelectorAll("[data-reuse-preset]")){
    button.addEventListener("click",()=>{
      const preset=button.getAttribute("data-reuse-preset");
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