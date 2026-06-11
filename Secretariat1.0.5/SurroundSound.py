# pip install pyaudio

import tkinter as tk
import pyaudio
import audioop
import collections

CHUNK = 512
RATE = 48000
WIDTH = 2
CHANNELS = 1
WINDOW_SECONDS = 5

CANVAS_WIDTH = 1200
GRAPH_HEIGHT = 450
SCALE_HEIGHT = 55
CANVAS_HEIGHT = GRAPH_HEIGHT + SCALE_HEIGHT

max_points = int(RATE / CHUNK * WINDOW_SECONDS)
values = collections.deque([0] * max_points, maxlen=max_points)

paused = False
mouse_x = None

zoom = 1.0
offset = 0.0  # 0.0 = left, 1.0 = right

p = pyaudio.PyAudio()

stream = p.open(
    format=pyaudio.paInt16,
    channels=CHANNELS,
    rate=RATE,
    input=True,
    frames_per_buffer=CHUNK
)

root = tk.Tk()
root.title("Live Microphone Volume - Zoomable")

canvas = tk.Canvas(root, width=CANVAS_WIDTH, height=CANVAS_HEIGHT, bg="black")
canvas.pack()

def clamp(v, lo, hi):
    return max(lo, min(v, hi))

def toggle_pause(event=None):
    global paused
    paused = not paused

def zoom_in(event=None):
    global zoom, offset
    zoom = clamp(zoom * 1.5, 1.0, 100.0)
    offset = clamp(offset, 0.0, max_offset())

def zoom_out(event=None):
    global zoom, offset
    zoom = clamp(zoom / 1.5, 1.0, 100.0)
    offset = clamp(offset, 0.0, max_offset())

def pan_left(event=None):
    global offset
    offset = clamp(offset - 0.05 / zoom, 0.0, max_offset())

def pan_right(event=None):
    global offset
    offset = clamp(offset + 0.05 / zoom, 0.0, max_offset())

def max_offset():
    return max(0.0, 1.0 - (1.0 / zoom))

def on_mouse_move(event):
    global mouse_x
    mouse_x = clamp(event.x, 0, CANVAS_WIDTH)
    draw_graph()

def on_mouse_leave(event):
    global mouse_x
    mouse_x = None
    draw_graph()

root.bind("<space>", toggle_pause)
root.bind("+", zoom_in)
root.bind("=", zoom_in)
root.bind("-", zoom_out)
root.bind("<Left>", pan_left)
root.bind("<Right>", pan_right)
canvas.bind("<Motion>", on_mouse_move)
canvas.bind("<Leave>", on_mouse_leave)

def get_visible_range():
    total = len(values)
    visible_count = max(2, int(total / zoom))
    start = int(offset * total)
    start = clamp(start, 0, max(0, total - visible_count))
    end = start + visible_count
    return start, end

def draw_graph():
    canvas.delete("all")

    points = list(values)
    start, end = get_visible_range()
    visible = points[start:end]

    if len(visible) > 1:
        for i in range(1, len(visible)):
            x1 = (i - 1) / (len(visible) - 1) * CANVAS_WIDTH
            y1 = GRAPH_HEIGHT - visible[i - 1] * GRAPH_HEIGHT
            x2 = i / (len(visible) - 1) * CANVAS_WIDTH
            y2 = GRAPH_HEIGHT - visible[i] * GRAPH_HEIGHT
            canvas.create_line(x1, y1, x2, y2, fill="lime", width=2)

    canvas.create_line(0, GRAPH_HEIGHT, CANVAS_WIDTH, GRAPH_HEIGHT, fill="white")

    visible_seconds = (end - start) * CHUNK / RATE
    start_ms_ago = (len(points) - start) * CHUNK / RATE * 1000
    end_ms_ago = (len(points) - end) * CHUNK / RATE * 1000

    ticks = 10
    for t in range(ticks + 1):
        x = t / ticks * CANVAS_WIDTH
        ms_ago = start_ms_ago - (t / ticks) * (start_ms_ago - end_ms_ago)

        canvas.create_line(x, GRAPH_HEIGHT, x, GRAPH_HEIGHT + 8, fill="white")
        canvas.create_text(
            x,
            GRAPH_HEIGHT + 24,
            text=f"{ms_ago:.1f} ms",
            fill="white",
            font=("Arial", 9)
        )

    if mouse_x is not None:
        frac = mouse_x / CANVAS_WIDTH
        exact_ms_ago = start_ms_ago - frac * (start_ms_ago - end_ms_ago)

        canvas.create_line(mouse_x, 0, mouse_x, GRAPH_HEIGHT, fill="yellow")
        canvas.create_text(
            mouse_x,
            15,
            text=f"{exact_ms_ago:.2f} ms ago",
            fill="yellow",
            font=("Arial", 11, "bold"),
            anchor="n"
        )

    status = f"zoom {zoom:.1f}x | +/- zoom | arrows pan | space pause"

    if paused:
        status += " | PAUSED"

    canvas.create_text(
        10,
        10,
        text=status,
        fill="yellow" if paused else "white",
        font=("Arial", 11, "bold"),
        anchor="nw"
    )

def update():
    if not paused:
        data = stream.read(CHUNK, exception_on_overflow=False)
        rms = audioop.rms(data, WIDTH)
        volume = min(rms / 3000, 1.0)
        values.append(volume)

    draw_graph()
    root.after(10, update)

def on_close():
    stream.stop_stream()
    stream.close()
    p.terminate()
    root.destroy()

root.protocol("WM_DELETE_WINDOW", on_close)
update()
root.mainloop()