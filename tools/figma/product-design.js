import ui from './product-ui.html';
import library from './node_modules/page-flip/dist/js/page-flip.browser.js';
import books from '../../data/showcase-books.json';

const color=h=>({r:parseInt(h.slice(1,3),16)/255,g:parseInt(h.slice(3,5),16)/255,b:parseInt(h.slice(5,7),16)/255});
const fill=h=>[{type:'SOLID',color:color(h)}];
let serif,sans,page;const targets={},pending=[],created=[],checks=[];
function text(parent,name,value,size=18,line=30,width=290){const n=figma.createText();n.name=name;n.fontName=serif;n.fontSize=size;n.lineHeight={unit:'PIXELS',value:line};n.characters=value;n.fills=fill('#41372E');parent.appendChild(n);n.resize(width,line);n.textAutoResize='HEIGHT';return n;}
function auto(parent,name,width){const f=figma.createFrame();parent.appendChild(f);f.name=name;f.layoutMode='VERTICAL';f.resize(width,1);f.primaryAxisSizingMode='AUTO';f.counterAxisSizingMode='FIXED';f.itemSpacing=14;f.fills=[];return f;}
function component(name,w,h){let c=page.children.find(n=>n.type==='COMPONENT'&&n.name===name);if(c)return c;c=figma.createComponent();page.appendChild(c);c.name=name;c.resize(w,h);c.fills=[];c.x=-1100;c.y=180+page.children.filter(n=>n.type==='COMPONENT').length*130;c.layoutMode='HORIZONTAL';c.primaryAxisAlignItems='CENTER';c.counterAxisAlignItems='CENTER';text(c,'文字','继续',18,28,w-24).textAlignHorizontal='CENTER';return c;}
let buttonTemplate,rowTemplate;
function button(parent,label,target,primary=false,width=290){const n=(primary?buttonTemplate:rowTemplate).createInstance();parent.appendChild(n);n.name=label;n.resize(width,52);const t=n.findOne(x=>x.type==='TEXT');t.characters=label;t.resize(width-24,28);t.textAutoResize='HEIGHT';if(target)pending.push([n,target]);return n;}
const chapter=books[0].chapters[0];
const specs=[
 ['login','13 · 登录｜打开家里的书','打开家里的回忆录','输入家人给您的邀请码。',[['邀请码','field'],['输入邀请码','input'],['翻开我的回忆录','create',true]],'登录'],
 ['create','14 · 建档｜写下称呼','写在这本书的扉页','这个称呼会出现在您的回忆录里。',[['怎么称呼您','field'],['填写您的称呼','input'],['补充背景与不想聊的话题 ›','background'],['开始这本回忆录','empty',true]],'扉页'],
 ['background','15 · 建档｜补充背景','让我们更懂您','这些资料可以留空，也可以以后补充。',[['出生年份 · 家乡','input'],['重要的家人','input'],['想聊的事','input'],['希望避开的话题','input'],['保存资料','create',true]],'个人资料'],
 ['shelf','16 · 家里的书｜切换回忆录','家里的回忆录','每本书，都只记录一位家人的经历。',[['为另一位家人开始一本','role']], '书架'],
 ['home','17 · 我的书｜已有章节',books[0].title,'李奶奶 口述 · '+books[0].chapters.length+' 章回忆',[['翻开阅读','li',true],['查看目录','toc'],['继续讲述','prepare'],['修改书名','title']], '我的回忆录'],
 ['title','18 · 书名｜编辑扉页','给这本书起个名字','书名会显示在自己的书封上。',[[books[0].title,'input'],['保存书名','home',true]],'书名'],
 ['photos','19 · 相册｜留住老照片','照片里的日子','放入一张老照片，从照片背后的故事聊起。',[['添加老照片','photo'],['JPG、PNG、WebP · 每张最多 10 MB','field'],['回到我的书','home']], '相册'],
 ['photo','20 · 相册｜照片详情','从这张照片聊起','照片展示区保留原始比例，点击可全屏查看。',[['选择照片后显示在这里','input'],['照片里的人、地点或故事','input'],['带着这张照片开始聊','prepare',true]],'照片'],
 ['prepare','21 · 访谈｜本人同意','今天，想从哪里讲起？','我是 AI 访谈者，陪您回忆走过的日子。',[['今天想聊什么？（可以不填）','input'],['□ 本人同意录音','consent'],['录音用来整理回忆录，随时可以结束。','field'],['请先勾选本人同意','field']],'讲述准备'],
 ['consent','21B · 访谈｜同意后开始','今天，想从哪里讲起？','我是 AI 访谈者，陪您回忆走过的日子。',[['今天想聊什么？（可以不填）','input'],['☑ 本人同意录音','prepare'],['录音用来整理回忆录，随时可以结束。','field'],['开始聊聊','live',true]],'讲述准备'],
 ['live','22 · 访谈｜正在倾听','我在听，您慢慢讲','随时可以打断我，接着说您想起的事。',[['通话时长','field'],['访谈中请保持页面打开。','field'],['结束这次访谈','saved',true]],'正在讲述'],
 ['saved','23 · 访谈｜录音已保存','这一段经历，留下了','双方原声按实际通话时间顺序保留。',[['听听完整问答','history'],['写成一段回忆','processing',true],['先回到我的书','home']], '讲述结束'],
 ['processing','24 · 整理｜进度','正在整理您的讲述','录音转写 → 整理故事 → 收进章节',[['可以离开页面，稍后回来查看。','field'],['返回我的书','home',true]],'整理进度'],
 ['failed','25 · 整理｜可恢复','这次整理还没有完成','已经保存的录音与文字仍然保留。',[['这里显示实际失败原因。','field'],['重新整理','processing',true],['查看录音与原话','history']], '整理进度'],
 ['review','26 · 原稿｜阅读与核对',chapter.title,'李奶奶 · 成稿节选',[[chapter.paragraphs[0].slice(0,66)+'…','paragraph'],['看看这段原话','quotes'],['需要核对的重要事件 ›','facts'],['听听完整访谈','history']], '原稿核对'],
 ['quotes','27 · 原话｜核对段落','这段文字从哪里来','原话与成稿保持对应，核对时再展开。',[['这里显示该段对应的原始转写。','input'],['需要修改','review'],['基本符合','review'],['写得很好','review']], '原话'],
 ['facts','28 · 核对｜重要事件','只核对重要的事','这里显示成稿中实际需要核实的事件。',[['事件 · 对应说明','input'],['确认这件事','review'],['这件事不采用','review'],['补充或修改说明','input'],['保存说明','review',true]], '核对'],
 ['history','29 · 访谈记录｜完整问答','亲口讲过的往事','每次访谈的录音、原话与成稿放在一起。',[['播放完整访谈','playback'],['查看访谈原话','quotes'],['查看本次成稿','review'],['按原话重新整理','processing'],['我的相册 ›','photos']], '访谈记录'],
 ['playback','30 · 原声｜同一时间轴','再听一遍那天的讲述','老人和访谈者按通话顺序播放。',[['播放 / 暂停','input'],['播放进度 · 拖动定位','input'],['查看对应原话','quotes'],['返回访谈记录','history']], '原声'],
 ['family','31 · 家人｜阅读管理','挑几篇故事，给家人看','谁能阅读、能否听原声，都由您决定。',[['选择展示内容','selection',true],['看看家人能读到什么','preview'],['邀请家人','invite'],['谁能看这本书','members'],['家人的留言','comments']], '家人'],
 ['selection','32 · 家人｜选择章节','选好愿意分享的故事','原话与核对事项只由管理者查看。',[['□ '+chapter.title,'field'],['□ 选择要展示的照片','field'],['□ 允许分享完整访谈原声','field'],['原声可能包含其他访谈内容。','field'],['预览给家人看的内容','preview',true]],'展示内容'],
 ['preview','33 · 家人｜展示预览','家人将看到这些内容','这里只出现您选中的章节和照片。',[['打开阅读预览','li'],['原声权限 · 单独授权','field'],['返回修改选择','selection'],['确认展示这些内容','family',true]], '展示预览'],
 ['invite','34 · 邀请｜同一 Wi-Fi','邀请一位家人','双方连接同一 Wi-Fi，并保持电脑运行。',[['□ 允许这位家人听原声','field'],['每份邀请供一人使用，7 天内有效。','field'],['生成邀请地址','inviteReady',true]],'邀请家人'],
 ['inviteReady','35 · 邀请｜复制地址','把邀请交给家人','请使用手机可访问的地址打开本页。',[['生成后显示实际邀请地址','input'],['长按全选并复制','field'],['撤销这份邀请','revoke'],['返回家人页','family']], '家人邀请'],
 ['members','36 · 权限｜家人名册','谁能看这本书','每位家人的原声权限单独管理。',[['家人加入后，称呼显示在这里。','field'],['阅读文字与照片 / 原声权限','field'],['移除阅读权限','revoke'],['取消家人阅读','revoke']], '阅读权限'],
 ['revoke','37 · 权限｜操作确认','确认收回这项权限？','已保存的正文与留言继续保留。',[['取消，继续保留','members'],['确认收回','family',true]], '确认操作'],
 ['join','38 · 家人加入｜称呼','有些故事，想讲给您听','留下家人熟悉的称呼，一起读这本书。',[['您的称呼','input'],['加入并阅读','shared',true],['请连接同一 Wi-Fi。','field']], '家人的邀请'],
 ['shared','39 · 家人阅读｜书封','家人的回忆录','这里只展示已获得阅读授权的内容。',[['翻开这本书','li',true],['听原声（获得授权时）','playback'],['给家人留句话','comment']], '家人阅读'],
 ['comments','40 · 留言｜家人的话','家人的留言','读完故事，留下一句想对家人说的话。',[['留言显示在对应章节下方。','field'],['写一条留言','comment'],['删除留言','deleteComment']], '留言'],
 ['comment','41 · 留言｜写下想说的话','读到这里，想对您说…','',[['填写留言内容','input'],['发送留言','comments',true],['返回阅读','shared']], '留言'],
 ['deleteComment','42 · 留言｜删除确认','删除这条留言？','删除后，家人阅读页也会移除这条留言。',[['保留留言','comments'],['确认删除','comments',true]], '确认操作'],
 ['account','43 · 我的｜书后附录','这本书的附录','资料、原声与帮助，都收在这里。',[['我的回忆录 · 切换书籍','shelf'],['老人资料','background'],['我的相册','photos'],['访谈记录','history'],['帮助与保存说明','help'],['退出登录','logout']], '我的'],
 ['help','44 · 帮助｜怎么使用','慢慢讲，就能开始','本人同意录音后开始访谈。结束后，选择“写成一段回忆”。',[['认识忆谈','cover'],['保存与隐私','privacy'],['返回我的','account']], '使用帮助'],
 ['privacy','45 · 隐私｜保存与分享','认真保存每一份回忆','资料保存在运行忆谈的电脑上；录音与照片加密保存。',[['国内豆包处理语音，通义听悟转写，千问整理文字。','paragraph'],['转写时临时上传录音，完成后移除云端临时副本。','paragraph'],['家人仅能阅读您确认展示的内容，原声需要单独授权。','paragraph'],['返回我的','account']], '保存与隐私'],
 ['logout','46 · 退出｜确认','暂时合上这本书？','回忆录继续保存在这台电脑上。',[['继续阅读','home'],['退出登录','cover',true]], '退出登录'],
];

async function main(){
 figma.showUI(ui.replace('/*LIBRARY*/',()=>library),{width:460,height:920});
 page=await figma.getNodeByIdAsync('28:257');await figma.setCurrentPageAsync(page);
 const available=await figma.listAvailableFontsAsync();serif=available.find(f=>f.fontName.family==='Noto Serif SC'&&f.fontName.style==='Medium').fontName;sans=available.find(f=>f.fontName.family==='Noto Sans SC'&&f.fontName.style==='Regular').fontName;
 const fonts=new Map([serif,sans].map(f=>[JSON.stringify(f),f]));for(const t of page.findAllWithCriteria({types:['TEXT']}))for(const s of t.getStyledTextSegments(['fontName']))fonts.set(JSON.stringify(s.fontName),s.fontName);for(const f of fonts.values()){figma.ui.postMessage({progress:'载入字体 '+f.family+' '+f.style});await figma.loadFontAsync(f);}
 const original=await figma.getNodeByIdAsync('28:260');const originalText=original.findAllWithCriteria({types:['TEXT']}).map(t=>t.characters).join('\n');
 targets.cover=original;targets.role=await figma.getNodeByIdAsync('28:403');targets.empty=await figma.getNodeByIdAsync('28:433');targets.li=await figma.getNodeByIdAsync('36:3640');targets.yang=await figma.getNodeByIdAsync('36:4601');targets.toc=await figma.getNodeByIdAsync('36:3660');
 buttonTemplate=component('忆谈组件 / 页外主操作',290,52);buttonTemplate.fills=fill('#876020');buttonTemplate.cornerRadius=14;buttonTemplate.findOne(n=>n.type==='TEXT').fills=fill('#FFFBF3');
 rowTemplate=component('忆谈组件 / 书内条目',290,52);rowTemplate.strokes=fill('#DCCDB6');rowTemplate.strokeWeight=.6;rowTemplate.cornerRadius=2;
 const template=await figma.getNodeByIdAsync('28:302');const paper=template.children.find(n=>n.name==='书页');
 const base=Math.max(...page.children.filter(n=>n.type==='FRAME'&&!n.name.startsWith('产品全流程 /')).map(n=>n.y+n.height))+260;
 for(const [index,s]of specs.entries()){
  const [key,name,title,description,rows,nav]=s;figma.ui.postMessage({progress:'排版 '+name});let f=page.children.find(n=>n.name==='产品全流程 / '+name);
  if(!f){f=figma.createFrame();page.appendChild(f);}else for(const child of [...f.children])child.remove();
  f.name='产品全流程 / '+name;f.resize(390,844);f.fills=fill('#E8DECC');f.clipsContent=true;f.x=100+index%6*510;f.y=base+Math.floor(index/6)*990;targets[key]=f;created.push(f);
  const status=text(f,'系统时间','9:41',14,22,80);status.x=28;status.y=18;const top=text(f,'页外导航',nav,16,26,250);top.x=112;top.y=59;top.textAlignHorizontal='RIGHT';
  const back=button(f,'‹ 返回',key==='login'?'role':key==='home'?'shelf':'home',false,88);back.x=16;back.y=46;
  const p=paper.clone();f.appendChild(p);p.x=16;p.y=100;
  const body=p.children.find(n=>n.name==='书页内容');for(const child of [...body.children])child.remove();
  for(const child of p.children.filter(n=>n.name.includes('整页回忆背景')||n.name.includes('正文渐变遮罩')))child.visible=false;
  body.resize(290,1);body.primaryAxisSizingMode='AUTO';body.itemSpacing=key==='home'?12:14;body.x=34;body.y=30;
  text(body,'篇目',nav,14,24);if(!['home','shared'].includes(key))text(body,'标题',title,27,40);
  if(['home','shared','shelf'].includes(key)){
   const covers=auto(body,'回忆录书封',290);covers.layoutMode='HORIZONTAL';covers.primaryAxisSizingMode='FIXED';covers.counterAxisSizingMode='FIXED';covers.resize(290,key==='shelf'?182:196);covers.primaryAxisAlignItems='CENTER';covers.itemSpacing=20;
   const templateCover=await figma.getNodeByIdAsync('36:3610');
   for(const [index,b]of (key==='shelf'?books:[books[0]]).entries()){const cover=templateCover.createInstance();covers.appendChild(cover);cover.name=b.title;cover.findOne(n=>n.type==='TEXT'&&n.name==='书名').characters=b.title;cover.findOne(n=>n.type==='TEXT'&&n.name==='讲述者').characters=b.author+' 口述';cover.rescale((key==='shelf'?130:140)/250);cover.layoutSizingHorizontal='FIXED';cover.layoutSizingVertical='FIXED';cover.resize(key==='shelf'?130:140,key==='shelf'?182:196);pending.push([cover,key==='shelf'&&index===1?'yang':'li']);}
  }
  if(description)text(body,'说明',description,18,30);
  for(const [label,dest,primary]of rows){if(dest==='field'||dest==='paragraph')text(body,label,label,dest==='paragraph'?17:16,dest==='paragraph'?29:26);else if(dest==='input'){const input=auto(body,'填写区域',290);input.paddingLeft=12;input.paddingRight=12;input.paddingTop=10;input.paddingBottom=10;input.fills=fill('#FFFBF3');input.strokes=fill('#DCCDB6');text(input,'填写内容',label,17,28,266);}else button(body,label,dest,primary);}
  for(const t of p.children.filter(n=>n.type==='TEXT')){t.characters=String(index+13).padStart(2,'0');t.y=576;}
  const navs=figma.createFrame();f.appendChild(navs);navs.name='页外书签导航';navs.layoutMode='HORIZONTAL';navs.resize(358,52);navs.fills=[];navs.itemSpacing=8;navs.x=16;navs.y=754;
  for(const [label,dest]of [['回忆录','home'],['家人','family'],['我的','account']])button(navs,label,dest,false,114);
  checks.push({name:f.name,bottom:body.y+body.height,limit:565});
 }
 for(const [node,key]of pending){if(!targets[key])throw new Error('未定义页面 '+key);let root=node;while(root.parent&&root.parent!==page)root=root.parent;if(root===targets[key]){node.findOne(n=>n.type==='TEXT').fills=fill('#876020');continue;}await node.setReactionsAsync([{trigger:{type:'ON_CLICK'},actions:[{type:'NODE',destinationId:targets[key].id,navigation:'NAVIGATE',transition:{type:'DISSOLVE',duration:.2,easing:{type:'EASE_IN_AND_OUT'}}}]}]);}
 const role=targets.role;for(const n of role.findAll(n=>n.type==='FRAME'&&/写我自己的故事|为妈妈记录|为爸爸记录|为其他家人记录/.test(n.name)))await n.setReactionsAsync([{trigger:{type:'ON_CLICK'},actions:[{type:'NODE',destinationId:targets.login.id,navigation:'NAVIGATE',transition:{type:'DISSOLVE',duration:.2,easing:{type:'EASE_IN_AND_OUT'}}}]}]);
 const own=targets.empty;for(const n of own.findAll(n=>n.type==='FRAME'&&n.name==='开始讲述'))await n.setReactionsAsync([{trigger:{type:'ON_CLICK'},actions:[{type:'NODE',destinationId:targets.prepare.id,navigation:'NAVIGATE',transition:{type:'DISSOLVE',duration:.2,easing:{type:'EASE_IN_AND_OUT'}}}]}]);
 for(const frame of page.children.filter(n=>n.type==='FRAME'&&/^\d\d ·/.test(n.name)))for(const n of frame.findAll(n=>'reactions'in n&&n.reactions.length)){
  const reactions=JSON.parse(JSON.stringify(n.reactions));let changed=false;
  for(const r of reactions)for(const a of r.actions||[])if(a.destinationId==='28:551'){a.destinationId=targets.prepare.id;changed=true;}
  if(frame===role&&n.name==='下一页 ›'){for(const r of reactions)for(const a of r.actions||[])if(a.type==='NODE'){a.destinationId=targets.login.id;changed=true;}}
  if(changed)await n.setReactionsAsync(reactions);
 }
 if(originalText!==original.findAllWithCriteria({types:['TEXT']}).map(t=>t.characters).join('\n'))throw new Error('原始封面被修改');
 const overflow=checks.filter(c=>c.bottom>c.limit);const overlaps=[];for(let i=0;i<created.length;i++)for(let j=i+1;j<created.length;j++){const a=created[i],b=created[j];if(a.x<b.x+b.width&&a.x+a.width>b.x&&a.y<b.y+b.height&&a.y+a.height>b.y)overlaps.push(a.name);}
 const guides=page.children.filter(n=>n.type==='FRAME'&&/^0[1-6] ·/.test(n.name)).sort((a,b)=>a.name.localeCompare(b.name));
 const report=['第二版原始首页：'+original.id+'，保留原节点、原背景与原书封。','产品补充页面：'+created.length+'；交互入口：'+pending.length,'画面重叠：'+overlaps.length,'正文超界：'+overflow.length,...overflow.map(c=>JSON.stringify(c)),...created.map(n=>n.name+' · '+n.id),'引导与阅读：纸张、文字、背景一起翻动；支持页角拖动、反向翻页与减少动态效果。','访谈、录音授权、资料输入、邀请确认：固定操作，翻页不会执行录音或分享。','Figma Present 用于页面跳转；连续卷页在本插件中体验。'];
 const pages=[];for(const g of guides){const leaf=g.children.find(n=>n.name==='书页');const data=await leaf.exportAsync({format:'PNG',constraint:{type:'SCALE',value:1}});pages.push('data:image/png;base64,'+figma.base64Encode(data));}
 const payload={pages,names:guides.map(n=>n.name),report};figma.ui.postMessage(payload);figma.ui.onmessage=m=>{if(m.type==='ready')figma.ui.postMessage(payload);};
 page.selection=[original,...created.slice(0,5)];figma.viewport.scrollAndZoomIntoView(created.slice(0,6));
}
main();
