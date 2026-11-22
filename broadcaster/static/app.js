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
  const refresh=document.getElementById("campaign-refresh");
  if(refresh && refresh.getAttribute("data-active")==="true"){
    window.setTimeout(()=>window.location.reload(),4000);
  }
});
