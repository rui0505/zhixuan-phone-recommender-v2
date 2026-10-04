import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const payload = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
const wb = Workbook.create();
const previewDir = path.join(path.dirname(payload.output), 'logs', 'xlsx_previews');
await fs.mkdir(previewDir, {recursive:true});
for (let index=0; index<payload.sheets.length; index++) {
  const data=payload.sheets[index];
  const sheet=wb.worksheets.add(data.title);
  sheet.showGridLines=false;
  const start=data.note ? 1 : 0;
  const matrix=[data.header, ...data.rows];
  sheet.getRangeByIndexes(start,0,matrix.length,data.header.length).values=matrix;
  const used=sheet.getRangeByIndexes(0,0,start+matrix.length,data.header.length);
  used.format.font={name:'Microsoft YaHei',size:10};
  used.format.verticalAlignment='center';
  used.format.rowHeight=22;
  used.format.wrapText=true;
  sheet.getRangeByIndexes(start,0,1,data.header.length).format={fill:'#DDEBF7',font:{name:'Microsoft YaHei',size:10,bold:true},rowHeight:32,wrapText:true,verticalAlignment:'center'};
  sheet.getRangeByIndexes(start,0,1,data.header.length).format.horizontalAlignment='center';
  if(data.note) {
    sheet.getCell(0,0).values=[[data.note]];
    sheet.getCell(0,0).format.font={name:'Microsoft YaHei',size:10,italic:true,color:'#666666'};
  }
  for(let col=0;col<data.header.length;col++) {
    const header=data.header[col];
    const width=data.widths[header] ?? Math.min(46,Math.max(14,...data.rows.slice(0,200).map(row=>String(row[col] ?? '').length))+2);
    const column=sheet.getRangeByIndexes(start,col,matrix.length,1);
    column.format.columnWidth=width;
    if(payload.text_cols.includes(header) || header.endsWith('ID') || header==='源记录' || header==='SHA256') column.setNumberFormat('@');
  }
  sheet.freezePanes.freezeRows(start+1);
  used.format.autofitRows();
  if(data.note) {
    sheet.getCell(0,0).format.wrapText=false;
    sheet.getCell(0,0).format.rowHeight=24;
  }
  sheet.freezePanes.freezeColumns(1);
  sheet.tables.add(sheet.getRangeByIndexes(start,0,matrix.length,data.header.length),true,`Dataset${index+1}`);
  sheet.getRangeByIndexes(start,0,1,data.header.length).format.font={name:'Microsoft YaHei',size:10,bold:true,color:'#000000'};
}
wb.recalculate();
const exported=await SpreadsheetFile.exportXlsx(wb);
await exported.save(payload.output);
// 只预览变更的规格及接口说明；其他视图在本次完整 QA 中也逐页查看。
for(const data of payload.sheets) {
  const preview=await wb.render({sheetName:data.title,range:data.title==='数据说明'?'A1:B15':'A1:F8',scale:1.5,format:'png'});
  await fs.writeFile(path.join(previewDir,`${data.title}.png`),new Uint8Array(await preview.arrayBuffer()));
}
console.log(JSON.stringify({xlsx:payload.output,sheets:payload.sheets.length,renderer:'artifact-tool'}));
