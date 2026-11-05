import type { PlanningSheet } from "./planning-results";

// Small OOXML writer for offline browser downloads; no network or private-file server.
// ZIP uses stored entries. Inline strings prevent formula injection and preserve identifiers.
const enc=new TextEncoder();
const xml=(v:string)=>v.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g,"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;");
const column=(index:number)=>{let s="";for(let n=index+1;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;};
function crc32(bytes:Uint8Array){let c=0xffffffff;for(const b of bytes){c^=b;for(let j=0;j<8;j++)c=(c>>>1)^((c&1)?0xedb88320:0);}return (c^0xffffffff)>>>0;}
function zip(files:[string,string][]) {
  const chunks:Uint8Array[]=[],central:Uint8Array[]=[];let offset=0;
  for(const [name,text] of files){
    const n=enc.encode(name),body=enc.encode(text),crc=crc32(body),h=new Uint8Array(30+n.length),v=new DataView(h.buffer);
    v.setUint32(0,0x04034b50,true);v.setUint16(4,20,true);v.setUint16(12,33,true);v.setUint32(14,crc,true);v.setUint32(18,body.length,true);v.setUint32(22,body.length,true);v.setUint16(26,n.length,true);h.set(n,30);
    const d=new Uint8Array(46+n.length),w=new DataView(d.buffer);w.setUint32(0,0x02014b50,true);w.setUint16(4,20,true);w.setUint16(6,20,true);w.setUint16(14,33,true);w.setUint32(16,crc,true);w.setUint32(20,body.length,true);w.setUint32(24,body.length,true);w.setUint16(28,n.length,true);w.setUint32(42,offset,true);d.set(n,46);
    chunks.push(h,body);central.push(d);offset+=h.length+body.length;
  }
  const size=central.reduce((n,c)=>n+c.length,0),end=new Uint8Array(22),e=new DataView(end.buffer);e.setUint32(0,0x06054b50,true);e.setUint16(8,files.length,true);e.setUint16(10,files.length,true);e.setUint32(12,size,true);e.setUint32(16,offset,true);
  const out=new Uint8Array(offset+size+22);let i=0;for(const c of [...chunks,...central,end]){out.set(c,i);i+=c.length;}return out;
}
export function planningXlsx(sheets:PlanningSheet[]):Uint8Array {
  if(!sheets.length||new Set(sheets.map(s=>s.name)).size!==sheets.length||sheets.some(s=>s.name.length>31||/[\\/?*\[\]:]/.test(s.name)))throw new Error("Hojas inválidas");
  const ns="http://schemas.openxmlformats.org/spreadsheetml/2006/main",rels="http://schemas.openxmlformats.org/package/2006/relationships";
  const files:[string,string][]=[
    ["[Content_Types].xml",`<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>${sheets.map((_,i)=>`<Override PartName="/xl/worksheets/sheet${i+1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join("")}</Types>`],
    ["_rels/.rels",`<Relationships xmlns="${rels}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`],
    ["xl/workbook.xml",`<workbook xmlns="${ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>${sheets.map((s,i)=>`<sheet name="${xml(s.name)}" sheetId="${i+1}" r:id="rId${i+1}"/>`).join("")}</sheets></workbook>`],
    ["xl/_rels/workbook.xml.rels",`<Relationships xmlns="${rels}">${sheets.map((_,i)=>`<Relationship Id="rId${i+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i+1}.xml"/>`).join("")}<Relationship Id="styles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>`],
    ["xl/styles.xml",`<styleSheet xmlns="${ns}"><numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.00####"/></numFmts><fonts count="2"><font><sz val="11"/><name val="Aptos"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Aptos"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF19384D"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="3"><xf fontId="0" fillId="0" borderId="0" xfId="0"/><xf fontId="1" fillId="2" borderId="0" xfId="0" applyFill="1" applyFont="1"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`],
  ];
  sheets.forEach((s,i)=>{
    const rows=[[s.title],[s.note],[],s.headers,...s.rows];
    const body=rows.map((r,y)=>`<row r="${y+1}">${r.map((v,x)=>v===null?"":typeof v==="number"?`<c r="${column(x)}${y+1}" s="2"><v>${Number.isFinite(v)?v: (()=>{throw new Error("Cantidad inválida");})()}</v></c>`:`<c r="${column(x)}${y+1}" t="inlineStr" s="${y===3?1:0}"><is><t xml:space="preserve">${xml(v.slice(0,32767))}</t></is></c>`).join("")}</row>`).join("");
    files.push([`xl/worksheets/sheet${i+1}.xml`,`<worksheet xmlns="${ns}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="4" topLeftCell="A5" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols><col min="1" max="${s.headers.length}" width="24" customWidth="1"/></cols><sheetData>${body}</sheetData><autoFilter ref="A4:${column(s.headers.length-1)}${rows.length}"/></worksheet>`]);
  });
  return zip(files);
}
