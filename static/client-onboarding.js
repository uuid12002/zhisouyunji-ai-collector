function downloadJSON(filename,value){
  const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
  const a=document.createElement('a');a.href=url;a.download=filename;a.click();
  setTimeout(()=>URL.revokeObjectURL(url),10000);
}
function renderExtra(result,parent){
  const copyButton=(label,value)=>{const b=document.createElement('button');b.textContent=label;b.style.margin='8px';b.disabled=!value;b.onclick=async()=>{try{await navigator.clipboard.writeText(value);b.textContent='已复制';}catch{b.textContent='复制失败，请手动选择内容';}};parent.append(b);};
  const shareHeading=document.createElement('h3');shareHeading.textContent='页面分享链接';parent.append(shareHeading);
  let shareValid=false;try{const url=new URL(result.share_url);shareValid=url.protocol==='https:'&&!url.username&&!url.password;}catch{}
  const shareItem=document.createElement(shareValid?'a':'p');shareItem.textContent=shareValid?result.share_url:'未取得页面分享链接';
  if(shareValid){shareItem.href=result.share_url;shareItem.target='_blank';shareItem.rel='noopener noreferrer';}parent.append(shareItem);
  copyButton('复制分享链接',shareValid?result.share_url:null);
  copyButton('复制全部回复',result.answer);
  const longImage=(result.artifacts||[]).find(a=>a.kind==='answer_long_image');
  if(longImage&&result.long_image_complete){const a=document.createElement('a');a.href=longImage.url;a.download='answer-long.png';a.textContent='下载完整回答长图';parent.append(a);}
  else{const p=document.createElement('p');p.textContent='完整长图尚未取得，请查看截图及完整性说明。';parent.append(p);}
  const button=document.createElement('button');button.textContent='下载本次结果 JSON';button.style.margin='12px';
  button.onclick=()=>downloadJSON('collection-result.json',result);parent.append(button);
  if(!(result.references||[]).length){const p=document.createElement('p');p.textContent='参考链接：本次未提取到参考来源。';parent.append(p);}
  const body=document.createElement('h3');body.textContent='网页回答正文';parent.append(body);
}
if(document.getElementById('add-device')){
 document.getElementById('add-device').onclick=async()=>{
  const name=prompt('客户电脑名称，如客户A-电脑01');if(!name)return;
  const button=document.getElementById('add-device');button.disabled=true;
  try{
   const device=await api('/api/v3/devices',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,max_slots:1})});
   const config={server_url:location.origin,device_id:device.device_id,device_key:device.device_key};
   const box=document.getElementById('device-secret');box.hidden=false;box.replaceChildren();
   const tip=document.createElement('p');tip.textContent='设备已创建。请下载接入配置交给对应客户；文件含设备密钥，只供这一台电脑使用。';box.append(tip);
   const save=document.createElement('button');save.textContent='下载客户接入配置';save.onclick=()=>downloadJSON('connection-'+device.device_id+'.json',config);box.append(save);
   refreshDevices();
  }catch(error){document.getElementById('device-error').textContent=error.message;}
  finally{button.disabled=false;}
 };
}
