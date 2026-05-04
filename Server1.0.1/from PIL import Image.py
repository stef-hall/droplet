from PIL import Image

def image_strip_to_gif(input_path, output_path, frame_count=10, duration_ms=90, target_height=280):
    """
    Split a horizontal sprite strip into equal vertical sections (left->right),
    use each section as a frame, and save as looping GIF.
    """
    img = Image.open(input_path).convert("RGBA")
    w, h = img.size
    frame_w = w // frame_count

    if frame_w == 0:
        raise ValueError("Image is too narrow for the requested frame count.")

    frames = []
    for i in range(frame_count):
        left = i * frame_w
        right = (i + 1) * frame_w if i < frame_count - 1 else w
        frame = img.crop((left, 0, right, h))
        frames.append(frame)

    # Make all frames same size (pad narrower last frame if needed)
    target_w = max(f.width for f in frames)
    target_h = max(f.height for f in frames)
    normalized = []
    for f in frames:
        canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
        canvas.paste(f, (0, 0))
        if target_height:
            scaled_w = max(1, round(canvas.width * (target_height / canvas.height)))
            canvas = canvas.resize((scaled_w, target_height), Image.Resampling.LANCZOS)
        normalized.append(canvas.convert("P", palette=Image.ADAPTIVE))

    normalized[0].save(
        output_path,
        save_all=True,
        append_images=normalized[1:],
        duration=duration_ms,
        loop=0,   # 0 = infinite loop
        optimize=False,
        disposal=2
    )

if __name__ == "__main__":
    image_strip_to_gif(
        input_path=r"C:\Users\stefa\Downloads\ChatGPT Image May 5, 2026, 12_33_45 AM.png",
        output_path="galload.gif",
        frame_count=10,
        duration_ms=40,
        target_height=280
    )
    print("Saved galload.gif")
