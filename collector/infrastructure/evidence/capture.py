"""Capture visible browser pixels in overlapping slices; never reconstruct screenshots."""

import asyncio
import json
import math
from PIL import Image


def stitch_answer(folder, files):
    """Join actual viewport pixels by element coordinates, removing overlap.

    Fail closed on missing rows or horizontal cropping. Keep original tiles as
    evidence even when a complete long image cannot be produced.
    """
    tiles = [f for f in files if f['name'].startswith('answer-') and '-wide-' not in f['name']]
    if not tiles:
        return None, '没有回答截图可生成长图。'
    height = math.ceil(tiles[0]['coverage']['element_height'])
    width = None
    end = 0
    rows = []
    for item in tiles:
        coverage = item['coverage']
        if not coverage.get('horizontal_complete', True):
            return None, '回答宽度被裁切，已保留分段截图，未生成完整长图。'
        start = round(coverage['start_y'])
        with Image.open(folder / item['name']) as source:
            tile = source.convert('RGB')
        if width is None:
            width = tile.width
            if width * height > 80_000_000:
                return None, '回答超出长图大小限制，已保留分段截图。'
        if tile.width != width or abs(coverage['element_height'] - height) > 1 or start > end:
            return None, '回答截图存在空隙或布局变化，已保留分段截图，未生成完整长图。'
        stop = min(height, start + tile.height)
        if stop > end:
            rows.append((tile.crop((0, max(0, end - start), width, stop - start)), end))
            end = stop
    if end < height or not width or width * height > 80_000_000:
        return None, '回答长图覆盖不足或超出图像大小限制，已保留分段截图。'
    image = Image.new('RGB', (width, height))
    for tile, top in rows:
        image.paste(tile, (0, top))
    name = 'answer-long.png'
    image.save(folder / name)
    if (folder / name).stat().st_size > 20 * 1024 * 1024:
        return None, '回答长图超过附件大小限制，已保留分段截图。'
    return {
        'name': name,
        'kind': 'answer_long_image',
        'mime': 'image/png',
        'width': width,
        'height': height,
        'method': 'joined_viewport_pixels',
    }, None


async def capture_segments(page, locator, folder, prefix):
    files, complete = await _capture_vertical(page, locator, folder, prefix)
    # A vertically complete answer can still contain horizontally clipped tables.
    nodes = await locator.evaluate_handle("""e=>[e,...e.querySelectorAll('*')].filter(n=>
      n.clientWidth>0 && n.scrollWidth>n.clientWidth+3 &&
      /auto|scroll|hidden|clip/.test(getComputedStyle(n).overflowX))""")
    try:
        count = await nodes.evaluate('a=>a.length')
        for index in range(count):
            handle = await nodes.get_property(str(index))
            node = handle.as_element()
            original = await node.evaluate('e=>e.scrollLeft')
            end = 0
            try:
                for part in range(40):
                    position = await node.evaluate(
                        """(e,x)=>{
                      e.scrollLeft=x;
                      return {left:e.scrollLeft,width:e.clientWidth,total:e.scrollWidth};
                    }""",
                        max(0, end - 64),
                    )
                    await asyncio.sleep(0.15)
                    slices, covered = await _capture_vertical(
                        page, node, folder, f'{prefix}-wide-{index + 1:02d}-{part + 1:02d}'
                    )
                    for item in slices:
                        item['coverage'].update(
                            start_x=position['left'],
                            end_x=position['left'] + position['width'],
                            content_width=position['total'],
                        )
                    files.extend(slices)
                    complete = complete and covered and position['left'] <= end + 3
                    new_end = position['left'] + position['width']
                    if new_end >= position['total'] - 3:
                        break
                    if new_end <= end:
                        complete = False
                        break
                    end = new_end
                else:
                    complete = False
            finally:
                await node.evaluate('(e,x)=>e.scrollLeft=x', original)
                await handle.dispose()
    finally:
        await nodes.dispose()
    return files, complete


async def _capture_vertical(page, locator, folder, prefix, *, centered=False):
    await page.mouse.move(0, 0)
    await asyncio.sleep(0.4)
    await locator.scroll_into_view_if_needed()
    if centered:
        await locator.evaluate(
            'e=>e.scrollIntoView({block:"center",inline:"nearest",behavior:"instant"})'
        )
    files = []
    ranges = []
    offset = 0
    previous = -1
    height = 0
    for index in range(80):
        if not (centered and index == 0):
            await locator.evaluate(
                """(e,offset)=>{
          let sc=e.parentElement;
          while(sc && !(sc.scrollHeight>sc.clientHeight+4 && /auto|scroll/.test(getComputedStyle(sc).overflowY))) sc=sc.parentElement;
          const r=e.getBoundingClientRect();
          const margin=Math.min(160,innerHeight/4);
          if(sc){const s=sc.getBoundingClientRect();sc.scrollTop+=r.top-s.top+offset-margin;}
          else window.scrollBy(0,r.top+offset-margin);
        }""",
                offset,
            )
        await asyncio.sleep(0.15)
        geometry = await locator.evaluate("""e=>{
          const r=e.getBoundingClientRect();
          let left=Math.max(0,r.left),right=Math.min(innerWidth,r.right),top=Math.max(0,r.top),bottom=Math.min(innerHeight,r.bottom);
          for(let p=e.parentElement;p;p=p.parentElement){
            if(p===document.body||p===document.documentElement)continue;
            const s=getComputedStyle(p),b=p.getBoundingClientRect();
            if(/auto|scroll|hidden|clip/.test(s.overflowY)){top=Math.max(top,b.top);bottom=Math.min(bottom,b.bottom);}
            if(/auto|scroll|hidden|clip/.test(s.overflowX)){left=Math.max(left,b.left);right=Math.min(right,b.right);}
          }
          const visible=y=>[left+3,(left+right)/2,right-3].every(x=>{const n=document.elementFromPoint(x,y);return n&&(n===e||e.contains(n));});
          // Find the longest unobstructed strip, avoiding sticky composer/header overlays.
          let best=[0,0],start=null;
          for(let y=Math.ceil(top);y<Math.floor(bottom);y++){
            // Align hit tests with integer CSS screenshot rows. Headed Chromium
            // can round fractional hit-test points up to the following row.
            if(visible(y)){if(start===null)start=y; if(y+1-start>best[1]-best[0])best=[start,y+1];}
            else start=null;
          }
          return {x:left,y:best[0],width:right-left,height:best[1]-best[0],
            start:best[0]-r.top,end:best[1]-r.top,total:r.height,
            element_width:r.width,horizontal_complete:left<=r.left+1 && right>=r.right-1};
        }""")
        height = geometry['total']
        (folder / (prefix + '-geometry.json')).write_text(json.dumps(geometry), encoding='utf-8')
        if geometry['width'] < 1 or geometry['height'] < 4 or geometry['end'] <= previous + 2:
            break
        name = f'{prefix}-{index + 1:03d}.png'
        clip = {k: geometry[k] for k in ('x', 'y', 'width', 'height')}
        await page.screenshot(path=str(folder / name), clip=clip, scale='css', timeout=30000)
        files.append(
            {
                'name': name,
                'kind': 'screenshot',
                'mime': 'image/png',
                'sequence': index + 1,
                'coverage': {
                    'start_y': geometry['start'],
                    'end_y': geometry['end'],
                    'element_height': height,
                    'horizontal_complete': geometry['horizontal_complete'],
                    'clip': clip,
                },
            }
        )
        ranges.append((geometry['start'], geometry['end']))
        previous = geometry['end']
        if previous >= height - 8:
            break
        offset = max(0, previous - 64)
    end = 0
    complete = bool(ranges)
    for start, stop in sorted(ranges):
        if start > end + 8:
            complete = False
        end = max(end, stop)
    complete = complete and end >= height - 8
    if not files and not centered:
        # Some layouts keep the question behind a fixed header after nearest-
        # ancestor scrolling. Retry with native scrolling of all ancestors.
        # Hit tests remain unchanged: never hide overlays or invent pixels.
        return await _capture_vertical(page, locator, folder, prefix, centered=True)
    return files, complete
