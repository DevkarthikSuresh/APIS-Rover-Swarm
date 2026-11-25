import sys
sys.dont_write_bytecode = True

from math import sqrt, atan2, pi
from controller import Robot, Motor, GPS, Compass, Emitter, Receiver, Brake, Lidar, Camera

import random
import time
import json
import csv
import os

from RoverBlockchain import Blockchain, Block
from RAFTConsensus import RAFTConsensus

TIME_STEP = 64
TARGET_POINTS_SIZE = 13
DISTANCE_TOLERANCE = 1.5
MAX_SPEED = 7.0
TURN_COEFFICIENT = 5.0
SAFE_DISTANCE = 6.0
SLOW_DISTANCE = 8.0
HARD_STOP_DISTANCE = 6.0

X, Y, Z = 0, 1, 2


class Vector:
    def __init__(self, u=0.0, v=0.0):
        self.u = u
        self.v = v


def modulus_double(a, m):
    div = int(a / m)
    r = a - div * m
    if r < 0.0:
        r += m
    return r


class MyRobot:
    def __init__(self):
        self.robot = Robot()
        self.robot_name = self.robot.getName()
        print(f"[INFO] Robot name: {self.robot_name}")

        self.motors = []
        motor_names = ["left motor 1", "left motor 2", "left motor 3", "left motor 4",
                       "right motor 1", "right motor 2", "right motor 3", "right motor 4"]
        for name in motor_names:
            motor = self.robot.getDevice(name)
            motor.setPosition(float('inf'))
            self.motors.append(motor)

        self.brakes = []
        brake_names = ["brake_left_1", "brake_left_2", "brake_left_3", "brake_left_4",
                       "brake_right_1", "brake_right_2", "brake_right_3", "brake_right_4"]
        for name in brake_names:
            brake = self.robot.getDevice(name)
            self.brakes.append(brake)

        self.gps = self.robot.getDevice("gps")
        self.gps.enable(TIME_STEP)

        self.compass = self.robot.getDevice("compass")
        self.compass.enable(TIME_STEP)

        # RAFT emitter/receiver (channel 1)
        self.emitter = self.robot.getDevice("raft_emitter")
        self.emitter.setChannel(1)
        self.emitter.setRange(-1)

        self.receiver = self.robot.getDevice("raft_receiver")
        self.receiver.setChannel(1)
        self.receiver.enable(TIME_STEP)

        # Navigation emitter/receiver (channel 2)
        self.position_emitter = self.robot.getDevice("position_emitter")
        self.position_emitter.setChannel(2)
        self.position_emitter.setRange(-1)

        self.position_receiver = self.robot.getDevice("position_receiver")
        self.position_receiver.setChannel(2)
        self.position_receiver.enable(TIME_STEP)
        # IMPORTANT: Receiver has NO setRange() in Webots.

        self.lidar = self.robot.getDevice("lidar")
        self.lidar.enable(TIME_STEP)
        self.lidar.enablePointCloud()

        self.camera = self.robot.getDevice("camera")
        self.camera.enable(TIME_STEP)

        # Targets are consistent with YOUR working code: (X, Y)
        self.targets = [
            Vector(-4.209318, 9.147717), Vector(0.946812, 9.404304), Vector(0.175989, -1.784311),
            Vector(-2.805353, -8.829694), Vector(-3.846730, -15.602851), Vector(-4.394915, -24.550777),
            Vector(-1.701877, -33.617226), Vector(-4.394915, -24.550777), Vector(-3.846730, -15.602851),
            Vector(-2.805353, -8.829694), Vector(0.175989, -1.784311), Vector(0.946812, 9.404304),
            Vector(-7.930821, 6.421292)
        ]
        self.current_target_index = 0

        self.stuck_counter = 0
        self.backoff_mode = False
        self.backoff_delay = random.randint(15, 25)
        self.cooldown_counter = 0

        self.priority = random.randint(1, 100)
        print(f"[{self.robot_name}] 🚦 Priority={self.priority}")

        self.blockchain = Blockchain()
        
        # Fixed peer list for 3 robots
        peer_names = ["moose", "moose(1)", "moose(2)", "moose(3)", "moose(4)", "moose(5)", "moose(6)", "moose(7)", "moose(8)", "moose(9)"]
        
        self.raft = RAFTConsensus(
            self.blockchain,
            self.robot_name,
            self.emitter,
            self.receiver,
            peer_names,
            self.priority,
            time_fn=self.robot.getTime,
            on_discovery_request=self.handle_discovery_request
        )

        # Discovery point (X,Y) — keep exactly as before
        self.discovery_x = 0.175989
        self.discovery_y = -1.784311
        self.discovery_sent = False
        self.discovery_wait_counter = 0
        self.waiting_for_discovery_block = False

        # Experiment 1 logging: leader timeline
        self.leader_log_file = "leader_log.csv"
        if not os.path.exists(self.leader_log_file):
            with open(self.leader_log_file, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["time", "rover", "leader", "term", "state"])

        self.robot.step(1000)
        self.robot_set_speed(MAX_SPEED, MAX_SPEED)

    def robot_set_speed(self, left_speed, right_speed):
        for i in range(4):
            self.motors[i].setVelocity(left_speed)
        for i in range(4, 8):
            self.motors[i].setVelocity(right_speed)

    def lock_motors_with_brake(self):
        for brake in self.brakes:
            brake.setDampingConstant(1e6)
        # extra safety: command 0 speed when "locking"
        self.robot_set_speed(0.0, 0.0)
        print(f"[{self.robot_name}]  Wheels locked — brake applied.")

    def unlock_motors(self):
        for brake in self.brakes:
            brake.setDampingConstant(0.0)

    def norm(self, v):
        return sqrt(v.u * v.u + v.v * v.v)

    def normalize(self, v):
        n = self.norm(v)
        if n != 0:
            v.u /= n
            v.v /= n

    def minus(self, v, v1, v2):
        v.u = v1.u - v2.u
        v.v = v1.v - v2.v

    def broadcast_position(self):
        gps_values = self.gps.getValues()
        # Keep same axis used by your working controller: X,Y
        msg = f"{self.robot_name},{gps_values[X]},{gps_values[Y]},{self.priority}"
        self.position_emitter.send(msg.encode('utf-8'))

    def receive_positions(self):
        positions = {}
        while self.position_receiver.getQueueLength() > 0:
            msg = self.position_receiver.getString()
            parts = msg.strip().split(',')
            name = parts[0].strip()
            if name != self.robot_name.strip():
                x = float(parts[1])
                y = float(parts[2])
                priority = int(parts[3])
                positions[name] = (x, y, priority)
            self.position_receiver.nextPacket()
        return positions

    def min_distance_to_other_robot(self, my_gps, other_positions):
        x, y = my_gps[X], my_gps[Y]
        min_dist = float('inf')
        closest_priority = 0
        for _, (ox, oy, prio) in other_positions.items():
            dx = ox - x
            dy = oy - y
            dist = sqrt(dx * dx + dy * dy)
            if dist < min_dist:
                min_dist = dist
                closest_priority = prio
        return min_dist, closest_priority

    def get_lidar_closest_obstacle_angle(self):
        ranges = self.lidar.getRangeImage()
        min_dist = float('inf')
        min_index = -1
        for i, r in enumerate(ranges):
            if r < min_dist:
                min_dist = r
                min_index = i
        angle = self.lidar.getFov() * (min_index / len(ranges)) - (self.lidar.getFov() / 2)
        return angle, min_dist


    def handle_discovery_request(self, payload, sender):
        if self.raft.state != "leader":
            return False

        block = self.blockchain.make_new_block(
            data=payload,
            proposer=self.robot_name,
            term=self.raft.current_term,
            timestamp=payload.get("sim_time", self.robot.getTime())
        )
        ok = self.raft.propose_block(block)
        if ok:
            self.discovery_sent = True
            print(f"[{self.robot_name}] 🚀 DiscoveryBlock proposed on behalf of {sender}!")
        else:
            print(f"[{self.robot_name}] ⚠️ Failed to propose DiscoveryBlock requested by {sender}.")
        return ok

    def log_leader_state(self):
        leader = self.raft.leader_id if self.raft.leader_id is not None else "None"
        with open(self.leader_log_file, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                round(self.robot.getTime(), 3),
                self.robot_name,
                leader,
                self.raft.current_term,
                self.raft.state
            ])

    def run_autopilot(self):
        left_speed = 0.0
        right_speed = 0.0

        position_3d = self.gps.getValues()
        north_3d = self.compass.getValues()

        self.raft.tick()
        self.log_leader_state()
        self.broadcast_position()
        other_positions = self.receive_positions()
        min_dist_to_other, closest_priority = self.min_distance_to_other_robot(position_3d, other_positions)

        position = Vector(position_3d[X], position_3d[Y])
        direction = Vector()
        self.minus(direction, self.targets[self.current_target_index], position)
        distance = self.norm(direction)
        self.normalize(direction)

        robot_angle = atan2(north_3d[0], north_3d[1])
        target_angle = atan2(direction.v, direction.u)
        beta = modulus_double(target_angle - robot_angle, 2.0 * pi) - pi

        if beta > 0:
            beta = pi - beta
        else:
            beta = -beta - pi

        # Discovery override protection — do not yield during discovery wait
        if self.discovery_wait_counter > 0 or self.waiting_for_discovery_block:
            should_yield = False
        else:
            should_yield = (min_dist_to_other < HARD_STOP_DISTANCE) and (self.priority < closest_priority)

        # Discovery waiting (stop rover in place)
        if self.discovery_wait_counter > 0:
            print(f"[{self.robot_name}] Waiting... discovery_wait_counter={self.discovery_wait_counter}")
            self.lock_motors_with_brake()
            self.discovery_wait_counter -= 1

            if self.discovery_wait_counter == 0 and not self.discovery_sent:
                payload = {
                    "event": "Discovery",
                    "location": [self.discovery_x, self.discovery_y],
                    "discovery": "SimulatedResource",
                    "reported_by": self.robot_name,
                    "sim_time": self.robot.getTime()
                }

                if self.raft.state == "leader":
                    if self.handle_discovery_request(payload, self.robot_name):
                        print(f"[{self.robot_name}] 🚀 DiscoveryBlock sent to swarm!")
                else:
                    leader = self.raft.leader_id
                    if leader:
                        self.emitter.send(json.dumps({
                            "type": "discovery_request",
                            "term": self.raft.current_term,
                            "from": self.robot_name,
                            "to": leader,
                            "payload": payload
                        }))
                        self.discovery_sent = True
                        print(f"[{self.robot_name}] 📡 Discovery forwarded to leader {leader}.")
                    else:
                        print(f"[{self.robot_name}] ℹ️ Discovery detected, but no leader is currently known.")
                
                # --- AUTO-SAVE ON DISCOVERY (LOGGING FIX) ---
                self.blockchain.save_to_file(f"blockchain_{self.robot_name}.json")
                
                self.waiting_for_discovery_block = False
                self.current_target_index = (self.current_target_index + 1) % TARGET_POINTS_SIZE
                self.unlock_motors()
            return

        if self.cooldown_counter > 0:
            self.cooldown_counter -= 1
            left_speed = MAX_SPEED * 0.3
            right_speed = MAX_SPEED * 0.3
            print(f"[{self.robot_name}] ⏳ Cooldown active ({self.cooldown_counter})")

        elif should_yield:
            self.stuck_counter += 1
            if self.stuck_counter > self.backoff_delay:
                self.backoff_mode = True
                print(f"[{self.robot_name}]  Initiating backoff!")

            if self.backoff_mode:
                left_speed = MAX_SPEED * 0.3
                right_speed = MAX_SPEED * 0.6
                if self.stuck_counter > (self.backoff_delay + 30):
                    self.backoff_mode = False
                    self.stuck_counter = 0
                    self.backoff_delay = random.randint(15, 25)
                    self.cooldown_counter = 50
                    print(f"[{self.robot_name}]  Ending backoff → entering Cooldown.")
            else:
                left_speed = 0.0
                right_speed = 0.0
                print(f"[{self.robot_name}] 🚨 STOP! Yielding to higher priority robot (prio={closest_priority})")

        elif min_dist_to_other < HARD_STOP_DISTANCE:
            self.stuck_counter = 0
            self.backoff_mode = False
            left_speed = MAX_SPEED * 0.4
            right_speed = MAX_SPEED * 0.6
            print(f"[{self.robot_name}]  Priority move with turn bias (prio={self.priority} > {closest_priority})")

        elif min_dist_to_other < SLOW_DISTANCE:
            self.stuck_counter = 0
            self.backoff_mode = False
            angle, lidar_dist = self.get_lidar_closest_obstacle_angle()
            if angle < 0:
                left_speed = MAX_SPEED * 0.8
                right_speed = MAX_SPEED * 0.4
                print(f"[{self.robot_name}]  LIDAR AVOIDANCE → sidestep right (angle={angle:.2f}, dist={lidar_dist:.2f})")
            else:
                left_speed = MAX_SPEED * 0.4
                right_speed = MAX_SPEED * 0.8
                print(f"[{self.robot_name}]  LIDAR AVOIDANCE → sidestep left (angle={angle:.2f}, dist={lidar_dist:.2f})")

        else:
            self.stuck_counter = 0
            self.backoff_mode = False
            if distance < DISTANCE_TOLERANCE:
                index = self.current_target_index + 1
                suffix = "th"
                if index == 1:
                    suffix = "st"
                elif index == 2:
                    suffix = "nd"
                elif index == 3:
                    suffix = "rd"
                print(f"{index}{suffix} target reached")

                target_x = self.targets[self.current_target_index].u
                target_y = self.targets[self.current_target_index].v

                # Only hard-stop at discovery point; otherwise proceed normally
                if (abs(target_x - self.discovery_x) < 0.1) and (abs(target_y - self.discovery_y) < 0.1) and not self.discovery_sent:
                    self.lock_motors_with_brake()
                    print(f"[{self.robot_name}] 🛰️ Discovery point reached! Preparing DiscoveryBlock...")
                    self.discovery_wait_counter = 150
                    self.waiting_for_discovery_block = True
                else:
                    # normal waypoint: do NOT lock brakes, just continue
                    self.current_target_index = (self.current_target_index + 1) % TARGET_POINTS_SIZE

            else:
                self.unlock_motors()
                left_speed = MAX_SPEED - pi + TURN_COEFFICIENT * beta
                right_speed = MAX_SPEED - pi - TURN_COEFFICIENT * beta
                print(f"[{self.robot_name}] Moving — distance={distance:.2f} | angle_diff={beta:.2f} | min_dist={min_dist_to_other:.2f}")

        self.robot_set_speed(left_speed, right_speed)

    def run(self):
        try:
            while self.robot.step(TIME_STEP) != -1:
                self.run_autopilot()
        except KeyboardInterrupt:
            print(f"[{self.robot_name}] FINAL BLOCKCHAIN:")
            
            # --- SAVE ON EXIT (LOGGING FIX) ---
            self.blockchain.save_to_file(f"blockchain_{self.robot_name}.json")
            print(f"[{self.robot_name}] 💾 Blockchain saved to blockchain_{self.robot_name}.json")
            
            for b in self.blockchain.chain:
                print(b.to_dict())


if __name__ == "__main__":
    controller = MyRobot()
    controller.run()