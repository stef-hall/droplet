import re
import tkinter as tk
from tkinter import scrolledtext

def calculate_tokens():
    text = input_box.get("1.0", tk.END)

    input_tokens = [int(x) for x in re.findall(r"input_tokens:\s*(\d+)", text)]
    cached_tokens = [int(x) for x in re.findall(r"cached_tokens:\s*(\d+)", text)]
    uncached_tokens = [int(x) for x in re.findall(r"uncached_tokens:\s*(\d+)", text)]

    total_input = sum(input_tokens)
    total_cached = sum(cached_tokens)
    total_uncached = sum(uncached_tokens) if uncached_tokens else total_input - total_cached
    model_calls = len(input_tokens)
    cache_rate = (total_cached / total_input * 100) if total_input else 0

    output = (
        f"Model calls: {model_calls}\n"
        f"Total input tokens: {total_input}\n"
        f"Cached tokens: {total_cached}\n"
        f"Uncached tokens: {total_uncached}\n"
        f"Cache hit rate: {cache_rate:.2f}%"
    )

    output_box.config(state="normal")
    output_box.delete("1.0", tk.END)
    output_box.insert(tk.END, output)
    output_box.config(state="normal")

def copy_output():
    output = output_box.get("1.0", tk.END).strip()
    root.clipboard_clear()
    root.clipboard_append(output)
    root.update()

root = tk.Tk()
root.title("Token Tally")
root.geometry("800x650")

input_box = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Consolas", 10))
input_box.pack(expand=True, fill="both", padx=10, pady=10)

button_frame = tk.Frame(root)
button_frame.pack(pady=5)

calculate_button = tk.Button(button_frame, text="Calculate Tokens", command=calculate_tokens)
calculate_button.pack(side=tk.LEFT, padx=5)

copy_button = tk.Button(button_frame, text="Copy Output", command=copy_output)
copy_button.pack(side=tk.LEFT, padx=5)

output_box = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Consolas", 12), height=6)
output_box.pack(fill="x", padx=10, pady=10)

root.mainloop()