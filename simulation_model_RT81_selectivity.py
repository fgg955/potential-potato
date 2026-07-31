
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import random
import time
import threading
from dataclasses import dataclass
from typing import Tuple, Optional
import csv
from datetime import datetime
import os

# Настройка matplotlib для русского текста
plt.rcParams['font.family'] = 'Segoe UI'
plt.rcParams['axes.unicode_minus'] = False


@dataclass
class CableParams:
    length: float = 2.1
    r0: float = 0.167
    x0: float = 0.073


@dataclass
class TransformerParams:
    S_nom: float = 1000
    uk: float = 6.064


@dataclass
class CTParams:
    ratio: int = 60
    saturation_start: float = 15
    max_error: float = 30


@dataclass
class RelayRT81Params:
    I_ust: float = 4.0
    t_ust: float = 4.0
    k_otsech: float = 8.0
    k_vozvrat: float = 0.8
    error_percent: float = 5.0


class RelayRT81:
    def __init__(self, params: RelayRT81Params, name: str = "Relay"):
        self.params = params
        self.name = name
        self.state = False
        self.trip_time = None
        self.start_time = None

    def get_actual_setting(self) -> float:
        error = random.uniform(-self.params.error_percent, self.params.error_percent) / 100
        return self.params.I_ust * (1 + error)

    def calculate_operating_time(self, I: float) -> Optional[float]:
        I_ust_actual = self.get_actual_setting()
        k_rat = I / I_ust_actual

        if k_rat >= self.params.k_otsech:
            return random.uniform(0.08, 0.1)

        if k_rat < 1.2:
            return None

        t = 1 / (20 * (((k_rat - 1) / 6) ** 1.8)) + self.params.t_ust
        return t

    def check(self, I: float, dt: float = 0.01) -> bool:
        if self.state:
            I_ust_actual = self.get_actual_setting()
            if I <= I_ust_actual * self.params.k_vozvrat:
                self.state = False
                self.trip_time = None
                self.start_time = None
            return self.state

        t_trip = self.calculate_operating_time(I)
        if t_trip is None:
            return False

        if self.start_time is None:
            self.start_time = time.time()
            self.trip_time = self.start_time + t_trip

        if time.time() >= self.trip_time:
            self.state = True
            return True

        return False

    def reset(self):
        self.state = False
        self.trip_time = None
        self.start_time = None


class PowerNetworkModel:
    def __init__(self, cable_params: CableParams, tr_params: TransformerParams, ct_params: CTParams):
        self.cable = cable_params
        self.transformer = tr_params
        self.ct = ct_params
        self.X_c = 0.549

    def calculate_kz_current(self, distance_percent: float, kz_type: str = "3ph") -> Tuple[float, float, float]:
        """Расчет тока КЗ"""
        U_nom = 6300
        L_kz = self.cable.length * distance_percent / 100
        R_kz = self.cable.r0 * L_kz
        X_kz = self.cable.x0 * L_kz
        Z_total = np.sqrt(R_kz ** 2 + (self.X_c + X_kz) ** 2)
        I_3ph = U_nom / (np.sqrt(3) * Z_total)
        I_primary = I_3ph * (np.sqrt(3) / 2) if kz_type == "2ph" else I_3ph

        I_secondary_ideal = I_primary / self.ct.ratio

        # Реалистичная модель насыщения
        if I_secondary_ideal <= self.ct.saturation_start:
            I_secondary = I_secondary_ideal
            error = 0
        else:
            k = I_secondary_ideal / self.ct.saturation_start
            saturation_factor = 1 - min(0.3, 0.15 * np.log10(k))
            I_secondary = I_secondary_ideal * saturation_factor
            error = (1 - saturation_factor) * 100

        return I_primary, I_secondary_ideal, I_secondary

    def get_current_range(self, kz_type: str = "3ph") -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        distances = np.linspace(0, 100, 100)
        currents_real = []
        currents_ideal = []
        errors = []

        for d in distances:
            _, I_ideal, I_real = self.calculate_kz_current(d, kz_type)
            currents_ideal.append(I_ideal)
            currents_real.append(I_real)
            if I_ideal > 0:
                error = (I_ideal - I_real) / I_ideal * 100
                errors.append(min(error, 30))
            else:
                errors.append(0)

        return distances, np.array(currents_real), np.array(currents_ideal), np.array(errors)


class RelayTestStandGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Испытание реле тока РТ-81/1 - Исследование селективности")
        self.root.geometry("1500x900")
        self.root.minsize(1300, 750)
        self.root.configure(bg='#e8ecf1')

        # Параметры
        self.cable_params = CableParams()
        self.tr_params = TransformerParams()
        self.ct_params = CTParams()
        self.relay_A_params = RelayRT81Params(I_ust=6.0, t_ust=2.0, k_otsech=8.0)
        self.relay_B_params = RelayRT81Params(I_ust=4.0, t_ust=1.0, k_otsech=8.0)

        # Модели
        self.network = PowerNetworkModel(self.cable_params, self.tr_params, self.ct_params)
        self.relay_A = RelayRT81(self.relay_A_params, "A")
        self.relay_B = RelayRT81(self.relay_B_params, "B")

        # Состояние
        self.is_testing = False
        self.test_thread = None
        self.update_after_id = None

        self._setup_ui()
        self._update_graphs()

    def _setup_ui(self):
        # Верхняя панель
        top = tk.Frame(self.root, bg='#2c3e50', height=45)
        top.pack(fill=tk.X)
        top.pack_propagate(False)

        tk.Label(top, text="РТ-81/1", font=("Segoe UI", 16, "bold"),
                 bg='#2c3e50', fg='white').pack(side=tk.LEFT, padx=15)
        tk.Label(top, text="Исследование селективности с учетом насыщения ТТ",
                 font=("Segoe UI", 10), bg='#2c3e50', fg='#bdc3c7').pack(side=tk.LEFT)

        # Основной контейнер
        main = tk.Frame(self.root, bg='#e8ecf1')
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # ===== ЛЕВАЯ ПАНЕЛЬ =====
        left = tk.Frame(main, bg='white', width=320, relief=tk.RAISED, bd=1)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
        left.pack_propagate(False)

        # Прокрутка
        left_canvas = tk.Canvas(left, bg='white', highlightthickness=0)
        left_scrollbar = tk.Scrollbar(left, orient="vertical", command=left_canvas.yview)
        left_scrollable = tk.Frame(left_canvas, bg='white')

        left_scrollable.bind("<Configure>", lambda e: left_canvas.configure(scrollregion=left_canvas.bbox("all")))
        left_canvas.create_window((0, 0), window=left_scrollable, anchor="nw", width=300)
        left_canvas.configure(yscrollcommand=left_scrollbar.set)

        left_canvas.pack(side="left", fill="both", expand=True)
        left_scrollbar.pack(side="right", fill="y")

        def _on_mousewheel(event):
            left_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        left_canvas.bind("<Enter>", lambda e: left_canvas.bind_all("<MouseWheel>", _on_mousewheel))
        left_canvas.bind("<Leave>", lambda e: left_canvas.unbind_all("<MouseWheel>"))

        # Параметры КЗ
        group1 = tk.LabelFrame(left_scrollable, text="Параметры КЗ", font=("Segoe UI", 9, "bold"), bg='white')
        group1.pack(fill=tk.X, padx=8, pady=8)

        # Расстояние
        dist_frame = tk.Frame(group1, bg='white')
        dist_frame.pack(fill=tk.X, padx=5, pady=5)
        tk.Label(dist_frame, text="Расстояние до КЗ:", bg='white', font=("Segoe UI", 8)).pack(side=tk.LEFT)
        self.dist_scale = tk.Scale(dist_frame, from_=0, to=100, orient=tk.HORIZONTAL, length=160,
                                   command=self._on_dist_change)
        self.dist_scale.set(50)
        self.dist_scale.pack(side=tk.LEFT, padx=5)
        self.dist_label = tk.Label(dist_frame, text="50%", width=4, bg='white', relief=tk.SUNKEN)
        self.dist_label.pack(side=tk.LEFT)

        # Вид КЗ
        kz_frame = tk.Frame(group1, bg='white')
        kz_frame.pack(fill=tk.X, padx=5, pady=5)
        tk.Label(kz_frame, text="Вид КЗ:", bg='white', font=("Segoe UI", 8)).pack(side=tk.LEFT)
        self.kz_type = tk.StringVar(value="3ph")
        tk.Radiobutton(kz_frame, text="3-фазное", variable=self.kz_type, value="3ph", bg='white',
                       command=self._update_graphs).pack(side=tk.LEFT, padx=(10, 5))
        tk.Radiobutton(kz_frame, text="2-фазное", variable=self.kz_type, value="2ph", bg='white',
                       command=self._update_graphs).pack(side=tk.LEFT)

        # Трансформатор тока
        group2 = tk.LabelFrame(left_scrollable, text="Трансформатор тока", font=("Segoe UI", 9, "bold"), bg='white')
        group2.pack(fill=tk.X, padx=8, pady=8)

        tt_frame1 = tk.Frame(group2, bg='white')
        tt_frame1.pack(fill=tk.X, padx=5, pady=3)
        tk.Label(tt_frame1, text="Kтт:", bg='white', font=("Segoe UI", 8)).pack(side=tk.LEFT)
        tk.Label(tt_frame1, text="300/5 = 60", bg='white', fg='#3498db', font=("Segoe UI", 8, "bold")).pack(
            side=tk.LEFT, padx=5)

        tt_frame2 = tk.Frame(group2, bg='white')
        tt_frame2.pack(fill=tk.X, padx=5, pady=3)
        tk.Label(tt_frame2, text="Начало насыщения (А):", bg='white', font=("Segoe UI", 8)).pack(side=tk.LEFT)
        self.sat_scale = tk.Scale(tt_frame2, from_=5, to=30, orient=tk.HORIZONTAL, length=140,
                                  command=self._on_sat_change)
        self.sat_scale.set(15)
        self.sat_scale.pack(side=tk.LEFT, padx=5)
        self.sat_label = tk.Label(tt_frame2, text="15", width=3, bg='white', relief=tk.SUNKEN)
        self.sat_label.pack(side=tk.LEFT)

        # Защита А
        group3 = tk.LabelFrame(left_scrollable, text="Защита А (вышестоящая)", font=("Segoe UI", 9, "bold"), bg='white',
                               fg='#2980b9')
        group3.pack(fill=tk.X, padx=8, pady=8)

        # Iуст
        iust_frame = tk.Frame(group3, bg='white')
        iust_frame.pack(fill=tk.X, padx=5, pady=3)
        tk.Label(iust_frame, text="Iуст (А):", bg='white', font=("Segoe UI", 8)).pack(side=tk.LEFT)
        self.iust_A = tk.IntVar(value=6)
        for val in [4, 5, 6, 7, 8, 9, 10]:
            tk.Radiobutton(iust_frame, text=str(val), variable=self.iust_A, value=val, bg='white',
                           command=self._update_graphs).pack(side=tk.LEFT, padx=2)

        # tуст
        tust_frame = tk.Frame(group3, bg='white')
        tust_frame.pack(fill=tk.X, padx=5, pady=3)
        tk.Label(tust_frame, text="tуст (с):", bg='white', font=("Segoe UI", 8)).pack(side=tk.LEFT)
        self.tust_A = tk.Scale(tust_frame, from_=0, to=4, orient=tk.HORIZONTAL, length=140, resolution=0.1,
                               command=self._update_graphs)
        self.tust_A.set(2.0)
        self.tust_A.pack(side=tk.LEFT, padx=5)
        self.tust_A_label = tk.Label(tust_frame, text="2.0", width=3, bg='white', relief=tk.SUNKEN)
        self.tust_A_label.pack(side=tk.LEFT)
        self.tust_A.configure(
            command=lambda v: [self.tust_A_label.config(text=f"{float(v):.1f}"), self._update_graphs()])

        # Котс
        kotsech_frame = tk.Frame(group3, bg='white')
        kotsech_frame.pack(fill=tk.X, padx=5, pady=3)
        tk.Label(kotsech_frame, text="Котс:", bg='white', font=("Segoe UI", 8)).pack(side=tk.LEFT)
        self.kotsech_A = tk.Scale(kotsech_frame, from_=2, to=8, orient=tk.HORIZONTAL, length=140, resolution=0.1,
                                  command=self._update_graphs)
        self.kotsech_A.set(8.0)
        self.kotsech_A.pack(side=tk.LEFT, padx=5)
        self.kotsech_A_label = tk.Label(kotsech_frame, text="8.0", width=3, bg='white', relief=tk.SUNKEN)
        self.kotsech_A_label.pack(side=tk.LEFT)
        self.kotsech_A.configure(
            command=lambda v: [self.kotsech_A_label.config(text=f"{float(v):.1f}"), self._update_graphs()])

        # Защита Б
        group4 = tk.LabelFrame(left_scrollable, text="Защита Б (нижестоящая)", font=("Segoe UI", 9, "bold"), bg='white',
                               fg='#27ae60')
        group4.pack(fill=tk.X, padx=8, pady=8)

        # Iуст
        iust_frameB = tk.Frame(group4, bg='white')
        iust_frameB.pack(fill=tk.X, padx=5, pady=3)
        tk.Label(iust_frameB, text="Iуст (А):", bg='white', font=("Segoe UI", 8)).pack(side=tk.LEFT)
        self.iust_B = tk.IntVar(value=4)
        for val in [4, 5, 6, 7, 8, 9, 10]:
            tk.Radiobutton(iust_frameB, text=str(val), variable=self.iust_B, value=val, bg='white',
                           command=self._update_graphs).pack(side=tk.LEFT, padx=2)

        # tуст
        tust_frameB = tk.Frame(group4, bg='white')
        tust_frameB.pack(fill=tk.X, padx=5, pady=3)
        tk.Label(tust_frameB, text="tуст (с):", bg='white', font=("Segoe UI", 8)).pack(side=tk.LEFT)
        self.tust_B = tk.Scale(tust_frameB, from_=0, to=4, orient=tk.HORIZONTAL, length=140, resolution=0.1,
                               command=self._update_graphs)
        self.tust_B.set(1.0)
        self.tust_B.pack(side=tk.LEFT, padx=5)
        self.tust_B_label = tk.Label(tust_frameB, text="1.0", width=3, bg='white', relief=tk.SUNKEN)
        self.tust_B_label.pack(side=tk.LEFT)
        self.tust_B.configure(
            command=lambda v: [self.tust_B_label.config(text=f"{float(v):.1f}"), self._update_graphs()])

        # Котс
        kotsech_frameB = tk.Frame(group4, bg='white')
        kotsech_frameB.pack(fill=tk.X, padx=5, pady=3)
        tk.Label(kotsech_frameB, text="Котс:", bg='white', font=("Segoe UI", 8)).pack(side=tk.LEFT)
        self.kotsech_B = tk.Scale(kotsech_frameB, from_=2, to=8, orient=tk.HORIZONTAL, length=140, resolution=0.1,
                                  command=self._update_graphs)
        self.kotsech_B.set(8.0)
        self.kotsech_B.pack(side=tk.LEFT, padx=5)
        self.kotsech_B_label = tk.Label(kotsech_frameB, text="8.0", width=3, bg='white', relief=tk.SUNKEN)
        self.kotsech_B_label.pack(side=tk.LEFT)
        self.kotsech_B.configure(
            command=lambda v: [self.kotsech_B_label.config(text=f"{float(v):.1f}"), self._update_graphs()])

        # Кнопки управления
        btn_frame = tk.Frame(left_scrollable, bg='white')
        btn_frame.pack(fill=tk.X, padx=8, pady=8)

        self.btn_start = tk.Button(btn_frame, text="ПУСК", bg='#27ae60', fg='white', font=("Segoe UI", 9, "bold"),
                                   command=self.start_test)
        self.btn_start.pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)

        self.btn_stop = tk.Button(btn_frame, text="СТОП", bg='#e74c3c', fg='white', font=("Segoe UI", 9, "bold"),
                                  command=self.stop_test, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)

        self.btn_reset = tk.Button(btn_frame, text="СБРОС", bg='#f39c12', fg='white', font=("Segoe UI", 9, "bold"),
                                   command=self.reset)
        self.btn_reset.pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)

        self.btn_save = tk.Button(btn_frame, text="СОХР", bg='#3498db', fg='white', font=("Segoe UI", 9, "bold"),
                                  command=self.save_data)
        self.btn_save.pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)

        self.btn_save_plot = tk.Button(btn_frame, text="ГРАФИК", bg='#9b59b6', fg='white', font=("Segoe UI", 9, "bold"),
                                       command=self.save_plots)
        self.btn_save_plot.pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)

        # Текущие значения
        ind_frame = tk.LabelFrame(left_scrollable, text="Текущие значения", font=("Segoe UI", 8, "bold"), bg='white')
        ind_frame.pack(fill=tk.X, padx=8, pady=8)

        val_frame = tk.Frame(ind_frame, bg='white')
        val_frame.pack(fill=tk.X, padx=5, pady=5)

        tk.Label(val_frame, text="Ток в реле:", bg='white', font=("Segoe UI", 8), width=12, anchor=tk.W).grid(row=0,
                                                                                                              column=0,
                                                                                                              padx=2,
                                                                                                              pady=2)
        self.current_label = tk.Label(val_frame, text="0.00 A", bg='#f0f0f0', width=10, relief=tk.SUNKEN,
                                      font=("Segoe UI", 9, "bold"))
        self.current_label.grid(row=0, column=1, padx=2)

        tk.Label(val_frame, text="Погрешность ТТ:", bg='white', font=("Segoe UI", 8), width=12, anchor=tk.W).grid(row=1,
                                                                                                                  column=0,
                                                                                                                  padx=2,
                                                                                                                  pady=2)
        self.error_label = tk.Label(val_frame, text="0%", bg='#f0f0f0', width=10, relief=tk.SUNKEN,
                                    font=("Segoe UI", 9, "bold"))
        self.error_label.grid(row=1, column=1, padx=2)

        # Журнал
        log_frame = tk.LabelFrame(left_scrollable, text="Журнал", font=("Segoe UI", 8, "bold"), bg='white')
        log_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.log_text = tk.Text(log_frame, height=8, font=("Consolas", 8), bg='#2c3e50', fg='#ecf0f1', wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)

        # ===== ПРАВАЯ ПАНЕЛЬ - ГРАФИКИ =====
        right = tk.Frame(main, bg='white', relief=tk.RAISED, bd=1)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # Создаем фигуру с 4 графиками
        self.fig = Figure(figsize=(11, 8), dpi=100, facecolor='white')
        self.fig.subplots_adjust(left=0.08, right=0.95, top=0.95, bottom=0.08, hspace=0.35, wspace=0.3)

        self.ax1 = self.fig.add_subplot(221)
        self.ax2 = self.fig.add_subplot(222)
        self.ax3 = self.fig.add_subplot(223)
        self.ax4 = self.fig.add_subplot(224)

        for ax in [self.ax1, self.ax2, self.ax3, self.ax4]:
            ax.set_facecolor('#fafafa')
            ax.grid(True, linestyle='--', alpha=0.3)
            ax.title.set_fontsize(10)
            ax.xaxis.label.set_fontsize(9)
            ax.yaxis.label.set_fontsize(9)

        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Статусбар
        status = tk.Frame(self.root, bg='#2c3e50', height=25)
        status.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(value="✅ Готов к испытаниям")
        tk.Label(status, textvariable=self.status_var, bg='#2c3e50', fg='white', anchor=tk.W,
                 font=("Segoe UI", 8)).pack(fill=tk.X, padx=10)

        self._update_current_values()

    def _on_dist_change(self, val):
        self.dist_label.config(text=f"{int(float(val))}%")
        self._update_current_values()
        self._update_graphs()

    def _on_sat_change(self, val):
        self.sat_label.config(text=f"{int(float(val))}")
        self._update_current_values()
        self._update_graphs()

    def _update_current_values(self):
        try:
            dist = self.dist_scale.get()
            kz = self.kz_type.get()
            self.ct_params.saturation_start = self.sat_scale.get()
            self.network = PowerNetworkModel(self.cable_params, self.tr_params, self.ct_params)
            _, _, I_real = self.network.calculate_kz_current(dist, kz)
            _, I_ideal, _ = self.network.calculate_kz_current(dist, kz)
            self.current_label.config(text=f"{I_real:.1f} A")

            if I_ideal > 0:
                error = (I_ideal - I_real) / I_ideal * 100
                error = min(error, 30)
                self.error_label.config(text=f"{error:.1f}%")
                if error > 10:
                    self.error_label.config(fg='red')
                elif error > 5:
                    self.error_label.config(fg='orange')
                else:
                    self.error_label.config(fg='green')
        except Exception as e:
            pass

    def _update_relay_params(self):
        self.relay_A_params.I_ust = self.iust_A.get()
        self.relay_A_params.t_ust = self.tust_A.get()
        self.relay_A_params.k_otsech = self.kotsech_A.get()

        self.relay_B_params.I_ust = self.iust_B.get()
        self.relay_B_params.t_ust = self.tust_B.get()
        self.relay_B_params.k_otsech = self.kotsech_B.get()

        self.relay_A = RelayRT81(self.relay_A_params, "A")
        self.relay_B = RelayRT81(self.relay_B_params, "B")

    def _update_graphs(self):
        self._update_relay_params()

        self.ct_params.saturation_start = self.sat_scale.get()
        self.network = PowerNetworkModel(self.cable_params, self.tr_params, self.ct_params)
        self._update_current_values()

        dist = self.dist_scale.get()
        kz = self.kz_type.get()
        _, _, I_real = self.network.calculate_kz_current(dist, kz)
        distances, currents_real, currents_ideal, errors = self.network.get_current_range(kz)

        # График 1: ВТХ
        self.ax1.clear()
        I_range = np.linspace(1, 35, 100)
        tA = []
        tB = []
        for I in I_range:
            tA.append(self.relay_A.calculate_operating_time(I) or 5)
            tB.append(self.relay_B.calculate_operating_time(I) or 5)

        self.ax1.plot(I_range, tA, 'b-', linewidth=2, label='Защита А (вышест.)')
        self.ax1.plot(I_range, tB, 'g-', linewidth=2, label='Защита Б (нижест.)')
        self.ax1.axvline(x=I_real, color='r', linestyle='--', linewidth=1.5, label=f'Ток КЗ = {I_real:.1f} А')
        self.ax1.axvline(x=self.ct_params.saturation_start, color='orange', linestyle=':', linewidth=1.5,
                         label=f'Насыщение = {self.ct_params.saturation_start} А')
        self.ax1.set_xlabel('Ток, А')
        self.ax1.set_ylabel('Время, с')
        self.ax1.set_title('Времятоковые характеристики')
        self.ax1.legend(loc='upper right', fontsize=7)
        self.ax1.grid(True, alpha=0.3)
        self.ax1.set_xlim(0, 35)
        self.ax1.set_ylim(0, 5)

        # График 2: Ток от расстояния
        self.ax2.clear()
        self.ax2.plot(distances, currents_ideal, 'b--', linewidth=1.5, label='Идеальный ток', alpha=0.7)
        self.ax2.plot(distances, currents_real, 'r-', linewidth=2, label='Реальный ток (с насыщением)')
        self.ax2.fill_between(distances, currents_real, currents_ideal, where=(currents_real < currents_ideal),
                              alpha=0.2, color='red')
        self.ax2.axvline(x=dist, color='b', linestyle='--', linewidth=1.5, label=f'Расстояние = {dist:.0f}%')
        self.ax2.axhline(y=self.ct_params.saturation_start, color='orange', linestyle=':', linewidth=1.5,
                         label=f'Насыщение = {self.ct_params.saturation_start} А')
        self.ax2.set_xlabel('Расстояние до КЗ, %')
        self.ax2.set_ylabel('Ток, А')
        self.ax2.set_title('Зависимость тока КЗ от расстояния')
        self.ax2.legend(loc='upper right', fontsize=7)
        self.ax2.grid(True, alpha=0.3)

        # График 3: Погрешность
        self.ax3.clear()
        self.ax3.plot(distances, errors, 'purple', linewidth=2, label='Погрешность ТТ')
        self.ax3.fill_between(distances, 0, errors, alpha=0.2, color='purple')
        self.ax3.axhline(y=10, color='r', linestyle='--', linewidth=1.5, label='Допустимая погрешность 10%')
        self.ax3.axvline(x=dist, color='b', linestyle='--', linewidth=1.5)
        self.ax3.set_xlabel('Расстояние до КЗ, %')
        self.ax3.set_ylabel('Погрешность, %')
        self.ax3.set_title('Погрешность трансформатора тока')
        self.ax3.set_ylim(0, 35)
        self.ax3.legend(loc='upper right', fontsize=7)
        self.ax3.grid(True, alpha=0.3)

        # График 4: Коэффициент передачи
        self.ax4.clear()
        k_factor = currents_real / currents_ideal
        k_factor = np.nan_to_num(k_factor, 1)
        self.ax4.plot(distances, k_factor, 'g-', linewidth=2, label='Коэффициент передачи')
        self.ax4.fill_between(distances, 0, k_factor, where=(k_factor < 1), alpha=0.2, color='red')
        self.ax4.axhline(y=1, color='b', linestyle='--', linewidth=1.5, label='Идеальный (1.0)')
        self.ax4.axhline(y=0.9, color='orange', linestyle=':', linewidth=1.5, label='Граница точности (0.9)')
        self.ax4.axvline(x=dist, color='b', linestyle='--', linewidth=1.5)
        self.ax4.set_xlabel('Расстояние до КЗ, %')
        self.ax4.set_ylabel('Коэффициент передачи')
        self.ax4.set_title('Влияние насыщения ТТ')
        self.ax4.set_ylim(0.5, 1.05)
        self.ax4.legend(loc='lower right', fontsize=7)
        self.ax4.grid(True, alpha=0.3)

        self.canvas.draw()

    def save_plots(self):
        """Сохранение всех графиков"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_dir = f"plots_{timestamp}"
        os.makedirs(save_dir, exist_ok=True)

        # Сохраняем общий график
        self.fig.savefig(f"{save_dir}/all_plots.png", dpi=150, bbox_inches='tight', facecolor='white')

        # Сохраняем параметры
        with open(f"{save_dir}/parameters.txt", 'w', encoding='utf-8') as f:
            f.write("ПАРАМЕТРЫ МОДЕЛИ\n")
            f.write("=" * 50 + "\n\n")
            f.write(f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(f"Расстояние КЗ: {self.dist_scale.get():.0f}%\n")
            f.write(f"Вид КЗ: {'3-фазное' if self.kz_type.get() == '3ph' else '2-фазное'}\n")
            f.write(f"Начало насыщения ТТ: {self.ct_params.saturation_start} А\n\n")
            f.write(
                f"Защита А: Iуст={self.relay_A_params.I_ust}А, tуст={self.relay_A_params.t_ust}с, Котс={self.relay_A_params.k_otsech}\n")
            f.write(
                f"Защита Б: Iуст={self.relay_B_params.I_ust}А, tуст={self.relay_B_params.t_ust}с, Котс={self.relay_B_params.k_otsech}\n")

        self.log(f"📸 Графики сохранены в папку: {save_dir}")
        messagebox.showinfo("Сохранение", f"Графики сохранены в папку:\n{save_dir}")

    def log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.log_text.see(tk.END)

    def start_test(self):
        if self.is_testing:
            return

        try:
            dist = self.dist_scale.get()
            kz = self.kz_type.get()
            _, _, I_real = self.network.calculate_kz_current(dist, kz)

            self.is_testing = True
            self.btn_start.config(state=tk.DISABLED)
            self.btn_stop.config(state=tk.NORMAL)

            self.log("=" * 40)
            self.log("▶ НАЧАЛО ИСПЫТАНИЯ")
            self.log(f"  Расстояние КЗ: {dist:.0f}%")
            self.log(f"  Ток в реле: {I_real:.1f} А")
            self.log(f"  Защита А: Iуст={self.relay_A_params.I_ust}А, tуст={self.relay_A_params.t_ust}с")
            self.log(f"  Защита Б: Iуст={self.relay_B_params.I_ust}А, tуст={self.relay_B_params.t_ust}с")

            self.test_thread = threading.Thread(target=self._run_test, args=(I_real,), daemon=True)
            self.test_thread.start()

        except Exception as e:
            self.log(f"Ошибка: {e}")

    def _run_test(self, I_test):
        self.relay_A.reset()
        self.relay_B.reset()

        start_time = time.time()
        trip_A = False
        trip_B = False
        trip_A_time = None
        trip_B_time = None

        dt = 0.05
        while self.is_testing and (not trip_A or not trip_B):
            trip_A = self.relay_A.check(I_test, dt)
            trip_B = self.relay_B.check(I_test, dt)

            if trip_A and trip_A_time is None:
                trip_A_time = time.time() - start_time
                self.log(f"✅ Защита А сработала через {trip_A_time:.2f} с")

            if trip_B and trip_B_time is None:
                trip_B_time = time.time() - start_time
                self.log(f"✅ Защита Б сработала через {trip_B_time:.2f} с")

            time.sleep(dt)

            if time.time() - start_time > 10:
                self.log("⏰ Превышено время ожидания")
                break

        self.log("-" * 40)
        if trip_A_time is not None and trip_B_time is not None:
            diff = abs(trip_A_time - trip_B_time)
            if trip_A_time < trip_B_time:
                self.log(f"📊 Первой сработала ЗАЩИТА А (Δt={diff:.2f}с)")
                if diff >= 0.3:
                    self.log("  ❌ СЕЛЕКТИВНОСТЬ НАРУШЕНА!")
                else:
                    self.log("  ✅ Селективность обеспечена")
            else:
                self.log(f"📊 Первой сработала ЗАЩИТА Б (Δt={diff:.2f}с)")
        elif trip_A_time is not None:
            self.log("📊 Сработала только ЗАЩИТА А - НАРУШЕНИЕ!")
        elif trip_B_time is not None:
            self.log("📊 Сработала только ЗАЩИТА Б")
        else:
            self.log("📊 Ни одна защита не сработала")

        self.is_testing = False
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.status_var.set("✅ Испытание завершено")

    def stop_test(self):
        self.is_testing = False
        self.status_var.set("⏹ Испытание остановлено")
        self.log("Испытание остановлено")
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)

    def reset(self):
        self.stop_test()
        self.relay_A.reset()
        self.relay_B.reset()
        self.status_var.set("✅ Готов к испытаниям")
        self.log("🔄 Сброс выполнен")

    def save_data(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"relay_test_{timestamp}.txt"
        with open(filename, 'w', encoding='utf-8') as f:
            f.write("ИСПЫТАНИЕ РЕЛЕ РТ-81/1\n")
            f.write("=" * 50 + "\n")
            f.write(f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(f"Расстояние КЗ: {self.dist_scale.get():.0f}%\n")
            f.write(f"Вид КЗ: {'3-фазное' if self.kz_type.get() == '3ph' else '2-фазное'}\n")
            f.write(f"Начало насыщения ТТ: {self.ct_params.saturation_start} А\n\n")
            f.write(
                f"Защита А: Iуст={self.relay_A_params.I_ust}А, tуст={self.relay_A_params.t_ust}с, Котс={self.relay_A_params.k_otsech}\n")
            f.write(
                f"Защита Б: Iуст={self.relay_B_params.I_ust}А, tуст={self.relay_B_params.t_ust}с, Котс={self.relay_B_params.k_otsech}\n")
        self.log(f"💾 Данные сохранены в {filename}")
        messagebox.showinfo("Сохранение", f"Сохранено: {filename}")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = RelayTestStandGUI()
    app.run()