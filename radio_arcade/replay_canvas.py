"""Read-only public match replay artwork for the Qt client."""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap


def replay_pixmap(view, width=630, height=370):
    image=QPixmap(width,height);image.fill(QColor("#0b1725"))
    p=QPainter(image);p.setRenderHint(QPainter.RenderHint.Antialiasing)
    kind=view.get("kind")
    p.setFont(QFont("DejaVu Sans",11))
    if kind in ("grid","connect-four","word-tiles","battleship","sea-warfare"):
        board=view.get("cells") if kind in ("grid","connect-four","word-tiles") else view.get("shots",[])
        cols=15 if kind=="word-tiles" else 10 if kind in ("battleship","sea-warfare") else view.get("width",3)
        rows=(len(board)+cols-1)//cols
        cell=min(28 if cols==15 else 48,(width-30)//cols,(height-32)//max(1,rows))
        x0=(width-cols*cell)//2;y0=(height-rows*cell)//2
        for i,item in enumerate(board):
            x=x0+(i%cols)*cell;y=y0+(i//cols)*cell
            value=item if isinstance(item,str) else str(item)
            bg="#17344a" if (i+cols)%2 else "#1d4056"
            if kind in ("battleship","sea-warfare"):
                bg="#e7edf3" if item==1 else "#922934" if item==2 else "#8d631c" if item>=3 else bg
            if kind=="word-tiles" and item and item!=" ":bg="#e8d8b7"
            p.setPen(QPen(QColor("#45647d"),1));p.setBrush(QColor(bg));p.drawRoundedRect(QRectF(x+1,y+1,cell-2,cell-2),3,3)
            if kind in ("battleship","sea-warfare"):
                value="○" if item==1 else "×" if item==2 else "S" if item>=3 else ""
            elif not value.strip():value=""
            p.setPen(QColor("#263043") if kind=="word-tiles" and item else QColor("#fafcff"))
            if value:p.drawText(QRectF(x,y,cell,cell),Qt.AlignmentFlag.AlignCenter,value[:2])
    elif kind=="piles":
        piles=view.get("piles",[])
        for i,n in enumerate(piles):
            x=30+i*max(80,(width-65)//max(1,len(piles)))
            h=min(height-70,n*24)
            p.setBrush(QColor("#00cfa5"));p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(x,height-h-35,55,h),5,5)
            p.setPen(QColor("#f0f5fa"));p.drawText(QRectF(x,height-32,55,27),Qt.AlignmentFlag.AlignCenter,str(n))
    elif kind=="hexxagon":
        cells=view.get("cells",[])
        for cell in cells:
            x=width/2+(cell["q"]+cell["r"]/2)*40
            y=height/2+cell["r"]*27
            p.setPen(QPen(QColor("#56718b"),2))
            p.setBrush(QColor("#00cfa5" if cell["owner"]==1 else "#fbbf24" if cell["owner"]==2 else "#18314b"))
            p.drawEllipse(QRectF(x-15,y-15,30,30))
    elif kind=="scorched-earth":
        terrain=view.get("terrain",[])
        sky=view.get("sky_height",0) or 1
        if terrain:
            xscale=width/max(1,len(terrain))
            for i,n in enumerate(terrain):
                h=min(height-35,max(0,float(n)/sky*(height-35)))
                p.setBrush(QColor("#2f664e"));p.setPen(Qt.PenStyle.NoPen)
                p.drawRect(QRectF(i*xscale,height-h-1,max(1,xscale+1),h))
            for i,t in enumerate(view.get("tanks",[])):
                x=t["x"]*xscale;y=height-(t.get("world_y",0)/sky)*(height-35)-10
                p.setBrush(QColor("#00cfa5" if i==0 else "#fbbf24"))
                p.drawRoundedRect(QRectF(x-10,y-8,20,14),4,4)
                p.setPen(QColor("#fff"));p.drawText(QRectF(x-30,y-35,60,20),Qt.AlignmentFlag.AlignCenter,str(t["hp"])+" HP")
    else:
        p.setPen(QColor("#e2e8f0"))
        p.drawText(QRectF(16,12,width-32,height-24),Qt.AlignmentFlag.AlignCenter,
                   "Replay available · "+str(view.get("kind","unknown"))+" game")
    p.end()
    return image
